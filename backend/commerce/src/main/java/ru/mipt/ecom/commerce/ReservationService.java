package ru.mipt.ecom.commerce;

import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
class ReservationService {
    final JdbcTemplate db;
    final NamedParameterJdbcTemplate named;
    final DomainEvents events;
    final TransactionTemplate tx;
    ReservationService(JdbcTemplate db,NamedParameterJdbcTemplate named,DomainEvents events,TransactionTemplate tx) {
        this.db=db; this.named=named; this.events=events; this.tx=tx;
    }
    // Caller holds the order lock. All writers acquire product and inventory locks in ascending order.
    void settle(UUID order,boolean paid) {
        var reservation=db.queryForMap("SELECT id,state FROM reservations WHERE order_id=? FOR UPDATE",order);
        if (!reservation.get("state").equals("ACTIVE")) return;
        UUID id=(UUID)reservation.get("id");
        var items=db.query("SELECT product_id,quantity FROM reservation_items WHERE reservation_id=? ORDER BY product_id",
            (r,n)->new CommerceService.Item(r.getLong(1),r.getInt(2)),id);
        if(!items.isEmpty()) named.queryForList("SELECT id FROM products WHERE id IN (:ids) ORDER BY id FOR UPDATE",Map.of("ids",items.stream().map(CommerceService.Item::productId).toList()));
        for(var item:items) {
            db.update("UPDATE inventory SET reserved=reserved-?,on_hand=on_hand-? WHERE product_id=?",item.quantity(),paid?item.quantity():0,item.productId());
            events.product(item.productId(),"InventoryChanged");
        }
        db.update("UPDATE reservations SET state=? WHERE id=?",paid?"CONFIRMED":"RELEASED",id);
        long version=db.queryForObject("UPDATE orders SET status=?,version=version+1 WHERE id=? RETURNING version",Long.class,paid?"PAID":"EXPIRED",order);
        events.emit(paid?"PaymentSucceeded":"OrderExpired","Order",order,version,Map.of("orderId",order.toString()));
    }
    Map<String,Integer> expire() {
        int count=0;
        // A separate transaction per order prevents holding unrelated product locks across the batch.
        for(int i=0;i<100;i++) {
            boolean found=Boolean.TRUE.equals(tx.execute(status -> {
                var ids=db.queryForList("SELECT id FROM orders WHERE status='PENDING_PAYMENT' AND expires_at<clock_timestamp() ORDER BY expires_at,id LIMIT 1 FOR UPDATE SKIP LOCKED",UUID.class);
                if(ids.isEmpty()) return false;
                settle(ids.getFirst(),false); return true;
            }));
            if(!found) break;
            count++;
        }
        return Map.of("expired",count);
    }
}
