package ru.mipt.ecom.commerce;

import io.micrometer.core.instrument.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.*;
import org.springframework.stereotype.Component;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
@Profile("observability")
@EnableScheduling
class MetricsConfiguration {}

@Configuration
@Profile("worker")
@org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication
class WorkerSecurity {
    @Bean SecurityFilterChain workerFilterChain(HttpSecurity http) throws Exception {
        return http.csrf(c->c.disable()).authorizeHttpRequests(a->a
            .requestMatchers(r->r.getLocalPort()==9091 && (r.getRequestURI().equals("/actuator/prometheus") || r.getRequestURI().startsWith("/actuator/health"))).permitAll()
            .anyRequest().denyAll()).build();
    }
}

@Component
@Profile("observability")
class BusinessMetrics {
    final JdbcTemplate db; final MeterRegistry meters;
    final Map<String,Double> values=new java.util.concurrent.ConcurrentHashMap<>();
    final AtomicLong rss=new AtomicLong();
    BusinessMetrics(JdbcTemplate db,MeterRegistry meters) {
        this.db=db; this.meters=meters;
        for(String key:List.of("outbox_pending","outbox_oldest_seconds","payments_pending","payment_oldest_seconds","refund_pending","refund_oldest_seconds","expired_reservations","media_pending","db_up","last_success_unixtime","inventory_violations")) {
            values.put(key,0d); meters.gauge("ecom."+key,values,v->v.get(key));
        }
        meters.gauge("ecom.process.rss.bytes",rss);
    }
    @Scheduled(fixedDelay=10000,initialDelay=1000) void refresh() {
        try {
            for(String line:Files.readAllLines(Path.of("/proc/self/status"))) if(line.startsWith("VmRSS:")) rss.set(Long.parseLong(line.trim().split("\\s+")[1])*1024);
        } catch(Exception ignored) {}
        try {
            var data=db.queryForMap("""
                SELECT (SELECT count(*) FROM outbox_events WHERE published_at IS NULL) AS outbox_pending,
                 (SELECT coalesce(extract(epoch FROM now()-min(created_at)),0) FROM outbox_events WHERE published_at IS NULL) AS outbox_oldest_seconds,
                 (SELECT count(*) FROM payments WHERE state='PENDING') AS payments_pending,
                 (SELECT coalesce(extract(epoch FROM now()-min(created_at)),0) FROM payments WHERE state='PENDING') AS payment_oldest_seconds,
                 (SELECT count(*) FROM payments WHERE state='REFUND_PENDING') AS refund_pending,
                 (SELECT coalesce(extract(epoch FROM now()-min(updated_at)),0) FROM payments WHERE state='REFUND_PENDING') AS refund_oldest_seconds,
                 (SELECT count(*) FROM orders WHERE status='PENDING_PAYMENT' AND expires_at<now()-interval '10 seconds') AS expired_reservations,
                 (SELECT count(*) FROM media_uploads WHERE state='PENDING' AND created_at<now()-interval '1 hour') AS media_pending
                """);
            data.forEach((key,value)->values.put(key,((Number)value).doubleValue()));
            values.put("db_up",1d); values.put("last_success_unixtime",System.currentTimeMillis()/1000d);
        } catch(Exception e) { values.put("db_up",0d); }
    }
    @Scheduled(fixedDelay=60000,initialDelay=20000) void invariants() {
        try {
            double invalid=db.queryForObject("""
                WITH active AS (SELECT ri.product_id,sum(ri.quantity) AS qty FROM reservation_items ri JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id)
                SELECT count(*) FROM inventory i LEFT JOIN active a ON a.product_id=i.product_id WHERE i.reserved<>coalesce(a.qty,0) OR i.reserved<0 OR i.reserved>i.on_hand
                """,Double.class);
            values.put("inventory_violations",invalid);
        } catch(Exception e) { values.put("inventory_violations",Double.NaN); }
    }
}
