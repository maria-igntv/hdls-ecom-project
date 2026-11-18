package ru.mipt.ecom.commerce;

import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
class PaymentService {
    final JdbcTemplate db;
    final TransactionTemplate tx;
    final ReservationService reservations;
    final ProviderClient provider;
    PaymentService(JdbcTemplate db,TransactionTemplate tx,ReservationService reservations,ProviderClient provider) {
        this.db=db; this.tx=tx; this.reservations=reservations; this.provider=provider;
    }
    Map<String,Object> initiate(long user,UUID order) {
        UUID id=tx.execute(status -> {
            var rows=db.queryForList("SELECT total_minor,status,expires_at<clock_timestamp() AS expired FROM orders WHERE id=? AND user_id=? FOR UPDATE",order,user);
            if(rows.isEmpty()) throw new CommerceService.ApiFailure(404,"ORDER_NOT_FOUND");
            var old=db.queryForList("SELECT id FROM payments WHERE order_id=?",UUID.class,order);
            if(!old.isEmpty()) return old.getFirst();
            var row=rows.getFirst();
            if(!row.get("status").equals("PENDING_PAYMENT") || Boolean.TRUE.equals(row.get("expired"))) throw new CommerceService.ApiFailure(409,"ORDER_EXPIRED");
            UUID payment=UUID.randomUUID();
            db.update("INSERT INTO payments(id,order_id,provider_key,amount_minor,state,trace_id) VALUES(?,?,?,?,'PENDING',?)",payment,order,payment.toString(),row.get("total_minor"),DomainEvents.trace());
            return payment;
        });
        reconcile(id); // HTTP happens after the preparation transaction commits.
        return view(user,order);
    }
    Map<String,Object> view(long user,UUID order) {
        var rows=db.queryForList("SELECT p.id AS \"paymentId\",p.state,p.amount_minor AS \"amountMinor\",p.updated_at AS \"updatedAt\" FROM payments p JOIN orders o ON o.id=p.order_id WHERE o.id=? AND o.user_id=?",order,user);
        if(rows.isEmpty()) throw new CommerceService.ApiFailure(404,"PAYMENT_NOT_FOUND");
        return rows.getFirst();
    }
    void reconcile(UUID payment) {
        var rows=db.queryForList("SELECT * FROM payments WHERE id=?",payment);
        if(rows.isEmpty()) throw new CommerceService.ApiFailure(404,"PAYMENT_NOT_FOUND");
        var row=rows.getFirst();
        String previousTrace=MDC.get("trace_id");
        MDC.put("trace_id",row.get("trace_id").toString());
        try {
            var snapshot=provider.ensure(payment,((Number)row.get("amount_minor")).longValue(),row.get("trace_id").toString());
            if(row.get("state").equals("REFUND_PENDING")) snapshot=provider.refund(payment,row.get("trace_id").toString());
            apply(payment,snapshot,null,null);
            db.update("UPDATE payments SET next_attempt=clock_timestamp()+interval '5 seconds',lease_until=NULL,last_error=NULL WHERE id=?",payment);
        } catch(Exception error) {
            db.update("UPDATE payments SET attempts=attempts+1,next_attempt=clock_timestamp()+ make_interval(secs => least(60, power(2,least(attempts,5))::int)),lease_until=NULL,last_error=? WHERE id=?",error.getClass().getSimpleName(),payment);
        } finally {
            if(previousTrace==null) MDC.remove("trace_id"); else MDC.put("trace_id",previousTrace);
        }
    }
    void webhook(UUID event,UUID payment,String hash) {
        // A callback is a hint; fetching the authoritative snapshot prevents out-of-order regression.
        var snapshot=provider.get(payment);
        apply(payment,snapshot,event,hash);
    }
    void apply(UUID payment,ProviderClient.Snapshot snapshot,UUID event,String hash) {
        tx.executeWithoutResult(status -> {
            var rows=db.queryForList("SELECT order_id FROM payments WHERE id=?",payment);
            if(rows.isEmpty()) throw new CommerceService.ApiFailure(404,"PAYMENT_NOT_FOUND");
            UUID order=(UUID)rows.getFirst().get("order_id");
            var o=db.queryForMap("SELECT status,expires_at<=clock_timestamp() AS expired FROM orders WHERE id=? FOR UPDATE",order);
            var p=db.queryForMap("SELECT state,amount_minor FROM payments WHERE id=? FOR UPDATE",payment);
            if(!snapshot.id().equals(payment) || snapshot.amountMinor()!=((Number)p.get("amount_minor")).longValue() || !snapshot.currency().equals("RUB")) throw new IllegalStateException("Provider amount/identity mismatch");
            if(event!=null) {
                int inserted=db.update("INSERT INTO payment_webhooks(event_id,payment_id,body_hash) VALUES(?,?,?) ON CONFLICT DO NOTHING",event,payment,hash);
                if(inserted==0) {
                    if(!hash.equals(db.queryForObject("SELECT body_hash FROM payment_webhooks WHERE event_id=?",String.class,event))) throw new CommerceService.ApiFailure(409,"WEBHOOK_CONFLICT");
                    return;
                }
            }
            String local=(String)p.get("state");
            String next=transition(local,snapshot.state(),o.get("status").equals("PENDING_PAYMENT") && !Boolean.TRUE.equals(o.get("expired")));
            if(next.equals("SUCCEEDED") && local.equals("PENDING")) reservations.settle(order,true);
            if(next.equals("REFUND_PENDING") && o.get("status").equals("PENDING_PAYMENT")) reservations.settle(order,false);
            db.update("UPDATE payments SET state=?,updated_at=clock_timestamp() WHERE id=?",next,payment);
        });
    }
    static String transition(String local,String remote,boolean liveReservation) {
        if(local.equals("REFUNDED") || local.equals("SUCCEEDED") || local.equals("FAILED")) return local;
        if(local.equals("REFUND_PENDING")) return remote.equals("REFUNDED")?"REFUNDED":"REFUND_PENDING";
        return switch(remote) {
            case "SUCCEEDED" -> liveReservation?"SUCCEEDED":"REFUND_PENDING";
            case "FAILED" -> "FAILED";
            case "REFUNDED" -> throw new IllegalStateException("Unrequested provider refund");
            case "PENDING" -> "PENDING";
            default -> throw new IllegalStateException("Unknown provider state");
        };
    }
    void reconcileBatch() {
        var ids=tx.execute(status -> db.queryForList("""
            WITH selected AS (SELECT id FROM payments WHERE state IN ('PENDING','REFUND_PENDING')
              AND next_attempt<=clock_timestamp() AND (lease_until IS NULL OR lease_until<clock_timestamp())
              ORDER BY next_attempt LIMIT 20 FOR UPDATE SKIP LOCKED)
            UPDATE payments SET lease_until=clock_timestamp()+interval '30 seconds' FROM selected
              WHERE payments.id=selected.id RETURNING payments.id
            """,UUID.class));
        for(UUID id:ids) reconcile(id);
    }
}
