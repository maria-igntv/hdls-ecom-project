package ru.mipt.ecom.commerce;

import java.util.*;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

@Component
@Profile("worker & !migrate")
class Maintenance {
    final JdbcTemplate db; final TransactionTemplate tx; final SearchProjection search;
    Maintenance(JdbcTemplate db,TransactionTemplate tx,SearchProjection search) { this.db=db; this.tx=tx; this.search=search; }
    @Scheduled(fixedDelay=60000,initialDelay=60000) void retention() {
        try {
            for(String query:List.of(
                "DELETE FROM outbox_events WHERE event_id IN (SELECT event_id FROM outbox_events WHERE published_at<now()-interval '7 days' ORDER BY published_at LIMIT 1000 FOR UPDATE SKIP LOCKED)",
                "DELETE FROM consumer_inbox WHERE (consumer,event_id) IN (SELECT consumer,event_id FROM consumer_inbox WHERE processed_at<now()-interval '7 days' ORDER BY processed_at LIMIT 1000 FOR UPDATE SKIP LOCKED)",
                "DELETE FROM payment_webhooks WHERE event_id IN (SELECT event_id FROM payment_webhooks WHERE processed_at<now()-interval '30 days' ORDER BY processed_at LIMIT 1000 FOR UPDATE SKIP LOCKED)",
                "DELETE FROM idempotency_keys WHERE (user_id,key) IN (SELECT user_id,key FROM idempotency_keys WHERE created_at<now()-interval '7 days' ORDER BY created_at LIMIT 1000 FOR UPDATE SKIP LOCKED)"
            )) tx.executeWithoutResult(status->db.update(query));
            var indexes=db.queryForList("SELECT name FROM search_indexes WHERE state='RETIRED' AND retired_at<now()-interval '1 day' AND name NOT IN (SELECT name FROM search_indexes WHERE state='RETIRED' ORDER BY retired_at DESC LIMIT 2) LIMIT 1",String.class);
            for(String index:indexes) {
                var aliases=search.call("GET","/"+index+"/_alias",null);
                if(aliases.path(index).path("aliases").size()!=0) continue;
                search.call("DELETE","/"+index,null);
                db.update("DELETE FROM search_indexes WHERE name=? AND state='RETIRED'",index);
            }
        } catch(Exception error) { org.slf4j.LoggerFactory.getLogger(Maintenance.class).warn("Retention deferred: {}",error.toString()); }
    }
}
