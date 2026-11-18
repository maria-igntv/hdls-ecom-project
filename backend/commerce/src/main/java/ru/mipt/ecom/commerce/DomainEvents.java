package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.*;
import org.slf4j.MDC;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.*;

@Component
class DomainEvents {
    final JdbcTemplate db;
    final ObjectMapper json;
    final ProductCache cache;
    DomainEvents(JdbcTemplate db,ObjectMapper json,ProductCache cache) { this.db=db; this.json=json; this.cache=cache; }
    static UUID trace() { try { return UUID.fromString(MDC.get("trace_id")); } catch(Exception e) { return UUID.randomUUID(); } }
    String encode(Object value) { try { return json.writeValueAsString(value); } catch(Exception e) { throw new IllegalStateException(e); } }
    void emit(String type,String aggregateType,Object id,long version,Map<String,Object> payload) {
        db.update("INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,trace_id) VALUES(?,?,?,?,?,?::jsonb,?)",
            UUID.randomUUID(),aggregateType,id.toString(),version,type,encode(payload),trace());
    }
    void invalidate(long id,long version) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { cache.invalidate(id,version); }
        });
    }
    void product(long id,String type) {
        long version=db.queryForObject("UPDATE products SET version=version+1 WHERE id=? RETURNING version",Long.class,id);
        emit(type,"Product",id,version,Map.of("productId",id,"version",version));
        invalidate(id,version);
    }
}
