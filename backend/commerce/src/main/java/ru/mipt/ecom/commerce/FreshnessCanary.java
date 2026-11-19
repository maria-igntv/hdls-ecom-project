package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
@Profile("worker & observability")
@ConditionalOnProperty(name="canary.enabled",havingValue="true")
class FreshnessCanary {
    final MeterRegistry meters; final ObjectMapper json; final String base;
    final AtomicLong lastSuccess=new AtomicLong();
    final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(300)).build();
    FreshnessCanary(MeterRegistry meters,ObjectMapper json,@Value("${canary.url:http://haproxy:8080}") String base) {
        this.meters=meters; this.json=json; this.base=base;
        meters.gauge("ecom.search.canary.last.success.unixtime",lastSuccess);
    }
    @Scheduled(fixedDelay=30000,initialDelay=30000) void probe() {
        long start=System.nanoTime(); boolean success=false;
        try {
            var update=HttpRequest.newBuilder(URI.create(base+"/api/admin/products/100000")).timeout(Duration.ofSeconds(3))
                .header("Authorization","Basic "+Base64.getEncoder().encodeToString("admin:demo-admin".getBytes(java.nio.charset.StandardCharsets.UTF_8)))
                .header("Content-Type","application/json").PUT(HttpRequest.BodyPublishers.ofString("{\"name\":\"monitorcanary\",\"priceMinor\":10010000,\"active\":true}")).build();
            var response=http.send(update,HttpResponse.BodyHandlers.ofString());
            if(response.statusCode()!=200) throw new IllegalStateException("Canary update HTTP "+response.statusCode());
            long version=json.readTree(response.body()).path("version").asLong();
            while(System.nanoTime()-start<Duration.ofSeconds(10).toNanos()) {
                var result=http.send(HttpRequest.newBuilder(URI.create(base+"/api/search?q=monitorcanary&limit=5")).timeout(Duration.ofSeconds(2)).GET().build(),HttpResponse.BodyHandlers.ofString());
                if(result.statusCode()==200) for(var item:json.readTree(result.body()).path("items")) if(item.path("id").asLong()==100000 && item.path("version").asLong()>=version) success=true;
                if(success) break;
                Thread.sleep(250);
            }
        } catch(Exception e) { if(e instanceof InterruptedException) Thread.currentThread().interrupt(); }
        io.micrometer.core.instrument.Timer.builder("ecom.search.visibility").tag("success",Boolean.toString(success)).publishPercentileHistogram().serviceLevelObjectives(Duration.ofSeconds(1),Duration.ofSeconds(2),Duration.ofSeconds(5),Duration.ofSeconds(10)).register(meters).record(System.nanoTime()-start,java.util.concurrent.TimeUnit.NANOSECONDS);
        if(success) lastSuccess.set(System.currentTimeMillis()/1000);
    }
}
