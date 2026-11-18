package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class ProviderClient {
    final java.util.concurrent.Semaphore calls=new java.util.concurrent.Semaphore(8);
    record Snapshot(UUID id,long amountMinor,String currency,String state) {}
    final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(500)).build();
    final String url,token;
    final ObjectMapper json;
    ProviderClient(@Value("${payment.url:http://localhost:18087}") String url,@Value("${payment.token:local-provider-token}") String token,ObjectMapper json) { this.url=url; this.token=token; this.json=json; }
    Snapshot ensure(UUID id,long amount,String trace) { return call("PUT","/payments/"+id,Map.of("amountMinor",amount,"currency","RUB"),trace,Snapshot.class); }
    Snapshot get(UUID id) { return call("GET","/payments/"+id,null,DomainEvents.trace().toString(),Snapshot.class); }
    Snapshot refund(UUID id,String trace) { return call("POST","/payments/"+id+"/refund",Map.of(),trace,Snapshot.class); }
    <T> T call(String method,String path,Object body,String trace,Class<T> type) {
        if(!calls.tryAcquire()) throw new IllegalStateException("Provider concurrency limit");
        try {
            var req=HttpRequest.newBuilder(URI.create(url+path)).timeout(Duration.ofSeconds(3))
                .header("Authorization","Bearer "+token).header("Content-Type","application/json").header("X-Request-ID",trace)
                .method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(body))).build();
            var response=http.send(req,HttpResponse.BodyHandlers.ofByteArray());
            if(response.statusCode()/100!=2) throw new IllegalStateException("Provider HTTP "+response.statusCode());
            return json.readValue(response.body(),type);
        } catch(InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
        catch(Exception e) { throw new IllegalStateException(e); }
        finally { calls.release(); }
    }
}
