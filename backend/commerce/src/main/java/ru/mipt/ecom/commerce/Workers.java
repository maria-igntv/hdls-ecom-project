package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.slf4j.*;
import org.springframework.context.annotation.*;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.*;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

@Configuration
@Profile("worker")
@EnableScheduling
class WorkerConfiguration {}

@Component
@Profile("worker")
class Workers {
    static final Logger LOG=LoggerFactory.getLogger(Workers.class);
    final JdbcTemplate db; final TransactionTemplate tx; final DomainEvents events; final ObjectMapper json;
    final BrokerGateway broker; final SearchProjection search; final ProviderClient provider; final PaymentService payments;
    final ReservationService reservations; final MediaService media; final boolean demo;
    final Map<String,Channel> consumers=new HashMap<>();
    Workers(JdbcTemplate db,TransactionTemplate tx,DomainEvents events,ObjectMapper json,BrokerGateway broker,SearchProjection search,ProviderClient provider,PaymentService payments,ReservationService reservations,MediaService media,Environment env) {
        this.db=db; this.tx=tx; this.events=events; this.json=json; this.broker=broker; this.search=search; this.provider=provider; this.payments=payments; this.reservations=reservations; this.media=media;
        demo=env.matchesProfiles("demo");
    }
    void guard(String name,Runnable task) { try { task.run(); } catch(Exception e) { LOG.warn("{} failed: {}",name,e.toString()); } }
    void fault(String name) {
        if(demo && db.update("DELETE FROM demo_faults WHERE name=?",name)>0) Runtime.getRuntime().halt(91);
    }
    @Scheduled(fixedDelay=1000) void expire() { guard("expiry",()->reservations.expire()); }
    @Scheduled(fixedDelay=1000) void reconcile() { guard("reconcile",payments::reconcileBatch); }
    @Scheduled(fixedDelay=100) void rebuild() { guard("rebuild",search::rebuildStep); }
    @Scheduled(fixedDelay=30000) void orphans() { guard("media cleanup",media::cleanup); }
    @Scheduled(fixedDelay=200) void publish() {
        guard("publisher",()-> {
            try {
                broker.declare();
                var rows=tx.execute(status -> db.queryForList("""
                    WITH picked AS (SELECT event_id FROM outbox_events WHERE published_at IS NULL
                      AND (lease_until IS NULL OR lease_until<clock_timestamp()) ORDER BY created_at,event_id LIMIT 100 FOR UPDATE SKIP LOCKED)
                    UPDATE outbox_events e SET lease_until=clock_timestamp()+interval '30 seconds',attempts=attempts+1
                      FROM picked WHERE e.event_id=picked.event_id RETURNING e.*
                    """));
                if(rows.isEmpty()) return;
                try(var ch=broker.channel()) {
                    for(var row:rows) {
                        Map<String,Object> envelope=new LinkedHashMap<>();
                        for(String key:List.of("event_id","aggregate_id","aggregate_type","aggregate_version","event_type","schema_version","trace_id")) envelope.put(key,row.get(key));
                        envelope.put("occurred_at",((java.sql.Timestamp)row.get("created_at")).toInstant().toString()); envelope.put("payload",json.readTree(row.get("payload").toString()));
                        BrokerGateway.publish(ch,"commerce.events",row.get("aggregate_type").toString(),events.encode(envelope).getBytes(StandardCharsets.UTF_8),Map.of());
                        fault("publisher_after_confirm");
                        db.update("UPDATE outbox_events SET published_at=clock_timestamp(),lease_until=NULL WHERE event_id=?",row.get("event_id"));
                    }
                }
            } catch(Exception e) { throw new IllegalStateException(e); }
        });
    }
    @Scheduled(fixedDelay=2000) synchronized void consumers() {
        guard("consumers",()-> {
            try {
                broker.declare();
                for(String queue:List.of("search","notifications")) {
                    Channel old=consumers.get(queue); if(old!=null && old.isOpen()) continue;
                    Channel ch=broker.channel(); ch.basicQos(8);
                    ch.basicConsume(queue,false,(tag,message)->consume(queue,ch,message),tag->{});
                    consumers.put(queue,ch);
                }
            } catch(Exception e) { throw new IllegalStateException(e); }
        });
    }
    void consume(String queue,Channel ch,Delivery message) {
        long tag=message.getEnvelope().getDeliveryTag();
        try {
            var event=json.readTree(message.getBody());
            if(event.path("schema_version").asInt()!=1) throw new IllegalArgumentException("Unsupported event schema");
            UUID id=UUID.fromString(event.path("event_id").asText());
            MDC.put("trace_id",event.path("trace_id").asText());
            if(db.queryForObject("SELECT count(*) FROM consumer_inbox WHERE consumer=? AND event_id=?",Integer.class,queue,id)==0) {
                if(queue.equals("search")) search.project(Long.parseLong(event.path("aggregate_id").asText()));
                else provider.call("PUT","/notifications/"+id,json.convertValue(event,Map.class),event.path("trace_id").asText(),Map.class);
                fault("consumer_after_effect_"+queue);
                db.update("INSERT INTO consumer_inbox(consumer,event_id) VALUES(?,?) ON CONFLICT DO NOTHING",queue,id);
            }
            ch.basicAck(tag,false);
        } catch(Exception error) {
            LOG.warn("Consumer {} failed: {}",queue,error.toString());
            try {
                long attempts=((Number)Optional.ofNullable(message.getProperties().getHeaders()).orElse(Map.of()).getOrDefault("x-delivery-count",0L)).longValue();
                if(attempts<5) { Thread.sleep(Math.min(1600,100L<<attempts)+new Random().nextInt(100)); ch.basicNack(tag,false,true); }
                else ch.basicReject(tag,false);
            } catch(Exception e) { try { ch.close(); } catch(Exception ignored) {} }
        } finally { MDC.remove("trace_id"); }
    }
}
