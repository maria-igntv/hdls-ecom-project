package ru.mipt.ecom.provider;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.*;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.*;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;

@SpringBootApplication
@EnableScheduling
public class ProviderApplication {
    public static void main(String[] args) { SpringApplication.run(ProviderApplication.class,args); }
    @Bean OncePerRequestFilter auth(@Value("${provider.token:local-provider-token}") String token) {
        return new OncePerRequestFilter() {
            @Override protected void doFilterInternal(HttpServletRequest req,HttpServletResponse res,FilterChain chain) throws ServletException,IOException {
                if(!req.getRequestURI().equals("/livez") && !("Bearer "+token).equals(req.getHeader("Authorization"))) { res.setStatus(401); return; }
                chain.doFilter(req,res);
            }
        };
    }
}

@RestController
class ProviderController {
    record Create(long amountMinor,String currency) {}
    record Change(String state) {}
    final JdbcTemplate db;
    final TransactionTemplate tx;
    final ObjectMapper json;
    final DemoFaults faults;
    ProviderController(JdbcTemplate db,TransactionTemplate tx,ObjectMapper json,DemoFaults faults) { this.db=db; this.tx=tx; this.json=json; this.faults=faults; }
    record Faults(int createDelayMs,boolean pauseCallbacks) {}
    @PutMapping("/demo/faults") Object faults(@RequestBody Faults body) {
        faults.createDelay.set(Math.clamp(body.createDelayMs(),0,10000)); faults.pauseCallbacks=body.pauseCallbacks(); return Map.of("configured",true);
    }
    @GetMapping("/livez") Object live() { return Map.of("status","UP"); }
    @Scheduled(fixedDelay=1000) void expirePending() {
        tx.executeWithoutResult(status -> {
            var ids=db.queryForList("SELECT id FROM payments WHERE state='PENDING' AND created_at<clock_timestamp()-interval '30 minutes' ORDER BY created_at,id LIMIT 100 FOR UPDATE SKIP LOCKED",UUID.class);
            for(UUID id:ids) change(id,"FAILED");
        });
    }
    Map<String,Object> view(UUID id) {
        var rows=db.queryForList("SELECT id,amount_minor AS \"amountMinor\",currency,state FROM payments WHERE id=?",id);
        if(rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        return rows.getFirst();
    }
    @GetMapping("/payments/{id}") Object get(@PathVariable UUID id) { return view(id); }
    @PutMapping("/payments/{id}") Object create(@PathVariable UUID id,@RequestBody Create body,@RequestHeader(value="X-Request-ID",defaultValue="demo") String trace,@RequestHeader(value="X-Demo-Delay-Ms",defaultValue="0") int delay) throws InterruptedException {
        if(body.amountMinor()<=0 || !"RUB".equals(body.currency())) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        db.update("INSERT INTO payments(id,amount_minor,currency,trace_id) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",id,body.amountMinor(),body.currency(),trace);
        var result=view(id);
        if(((Number)result.get("amountMinor")).longValue()!=body.amountMinor() || !result.get("currency").equals(body.currency())) throw new ResponseStatusException(HttpStatus.CONFLICT);
        // Demo fault: the operation has committed, but its response can miss the caller's deadline.
        int pause=Math.max(delay,faults.createDelay.getAndSet(0));
        if(pause>0) Thread.sleep(Math.min(pause,10000));
        return result;
    }
    @PostMapping("/payments/{id}/refund") Object refund(@PathVariable UUID id) { return change(id,"REFUNDED"); }
    @PostMapping("/demo/payments/{id}/state") Object demo(@PathVariable UUID id,@RequestBody Change state) {
        if(!Set.of("SUCCEEDED","FAILED").contains(state.state())) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        return change(id,state.state());
    }
    Object change(UUID id,String next) {
        return tx.execute(status -> {
            var rows=db.queryForList("SELECT state FROM payments WHERE id=? FOR UPDATE",String.class,id);
            if(rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND);
            String old=rows.getFirst();
            if(old.equals(next)) return view(id);
            if(!(old.equals("PENDING") && Set.of("SUCCEEDED","FAILED").contains(next) || old.equals("SUCCEEDED") && next.equals("REFUNDED"))) throw new ResponseStatusException(HttpStatus.CONFLICT);
            db.update("UPDATE payments SET state=? WHERE id=?",next,id);
            UUID event=UUID.randomUUID();
            try { db.update("INSERT INTO callbacks(event_id,payment_id,body) VALUES(?,?,?)",event,id,json.writeValueAsString(Map.of("eventId",event,"paymentId",id))); }
            catch(Exception e) { throw new IllegalStateException(e); }
            return view(id);
        });
    }
    @PutMapping("/notifications/{id}") Object notification(@PathVariable UUID id,@RequestBody Map<String,Object> body) throws Exception {
        db.update("INSERT INTO notifications(event_id,body) VALUES(?,?::jsonb) ON CONFLICT DO NOTHING",id,json.writeValueAsString(body));
        return Map.of("accepted",true);
    }
}

@Component
class CallbackSender {
    final JdbcTemplate db;
    final String url,secret;
    final DemoFaults faults;
    final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(500)).build();
    CallbackSender(JdbcTemplate db,@Value("${provider.callback:http://haproxy:8080/api/payments/webhook}") String url,@Value("${provider.webhook-secret:local-webhook-secret}") String secret,DemoFaults faults) { this.db=db; this.url=url; this.secret=secret; this.faults=faults; }
    @Scheduled(fixedDelay=1000) void send() {
        if(faults.pauseCallbacks) return;
        for(var row:db.queryForList("SELECT c.event_id,c.body,p.trace_id FROM callbacks c JOIN payments p ON p.id=c.payment_id WHERE c.sent_at IS NULL AND c.next_attempt<=now() ORDER BY c.next_attempt LIMIT 20")) {
            try {
                long timestamp=Instant.now().getEpochSecond();
                byte[] body=row.get("body").toString().getBytes(StandardCharsets.UTF_8);
                var mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
                mac.update((timestamp+".").getBytes(StandardCharsets.UTF_8));
                var req=HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(10)).header("Content-Type","application/json")
                    .header("X-Request-ID",row.get("trace_id").toString()).header("X-Payment-Timestamp",Long.toString(timestamp)).header("X-Payment-Signature",HexFormat.of().formatHex(mac.doFinal(body)))
                    .POST(HttpRequest.BodyPublishers.ofByteArray(body)).build();
                if(http.send(req,HttpResponse.BodyHandlers.discarding()).statusCode()/100!=2) throw new IOException("Callback not accepted");
                db.update("UPDATE callbacks SET sent_at=now() WHERE event_id=?",row.get("event_id"));
            } catch(Exception e) {
                if(e instanceof InterruptedException) Thread.currentThread().interrupt();
                db.update("UPDATE callbacks SET attempts=attempts+1,next_attempt=now()+make_interval(secs=>least(60,power(2,least(attempts,5))::int)) WHERE event_id=?",row.get("event_id"));
            }
        }
    }
}

@Component
class DemoFaults {
    final java.util.concurrent.atomic.AtomicInteger createDelay=new java.util.concurrent.atomic.AtomicInteger();
    volatile boolean pauseCallbacks;
}
