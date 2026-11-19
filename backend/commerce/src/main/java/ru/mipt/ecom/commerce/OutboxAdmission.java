package ru.mipt.ecom.commerce;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class OutboxAdmission {
    final JdbcTemplate db; final long maxPending,maxAge;
    private volatile long nextCheck;
    private volatile boolean blocked;
    OutboxAdmission(JdbcTemplate db,@Value("${outbox.max-pending:1000000}") long maxPending,@Value("${outbox.max-age-seconds:21600}") long maxAge) {
        this.db=db; this.maxPending=maxPending; this.maxAge=maxAge;
    }
    synchronized void refresh() {
        if(System.nanoTime()<nextCheck) return;
        var row=db.queryForMap("SELECT count(*) AS pending,coalesce(extract(epoch FROM clock_timestamp()-min(created_at)),0) AS age FROM (SELECT created_at FROM outbox_events WHERE published_at IS NULL ORDER BY created_at LIMIT ?) e",maxPending);
        blocked=((Number)row.get("pending")).longValue()>=maxPending || ((Number)row.get("age")).doubleValue()>=maxAge;
        nextCheck=System.nanoTime()+java.time.Duration.ofSeconds(5).toNanos();
    }
    void check() {
        refresh();
        if(blocked) throw new CommerceService.ApiFailure(503,"OUTBOX_CAPACITY");
    }
}
