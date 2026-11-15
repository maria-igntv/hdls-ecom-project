package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
class CommerceService {
    record Product(long id,String sku,long categoryId,String name,String description,long priceMinor,
                   String currency,int available,boolean active,long version) {}
    record Result(int status,Map<String,Object> body) {}
    record Item(long productId,int quantity) {}
    static class ApiFailure extends RuntimeException {
        final int status; final String code;
        ApiFailure(int status,String code) { super(code); this.status=status; this.code=code; }
    }
    private final JdbcTemplate db;
    private final NamedParameterJdbcTemplate named;
    private final ProductCache cache;
    private final ObjectMapper json;
    CommerceService(JdbcTemplate db,NamedParameterJdbcTemplate named,ProductCache cache,ObjectMapper json) {
        this.db=db; this.named=named; this.cache=cache; this.json=json;
    }
    private static Product product(ResultSet r,int n) throws SQLException {
        return new Product(r.getLong("id"),r.getString("sku"),r.getLong("category_id"),r.getString("name"),
            r.getString("description"),r.getLong("price_minor"),r.getString("currency"),r.getInt("available"),r.getBoolean("active"),r.getLong("version"));
    }
    private static final String PRODUCT = """
        SELECT p.id,p.sku,p.category_id,p.name,p.description,p.active,p.version,
               pr.price_minor,pr.currency,i.on_hand-i.reserved AS available
        FROM products p JOIN product_prices pr ON pr.product_id=p.id JOIN inventory i ON i.product_id=p.id
        """;
    Product card(long id) {
        return cache.get(id,() -> db.query(PRODUCT+" WHERE p.id=? AND p.active",CommerceService::product,id)
            .stream().findFirst().orElseThrow(() -> new ApiFailure(404,"PRODUCT_NOT_FOUND")));
    }
    List<Product> category(long category,long after,int limit) {
        return db.query("""
            WITH selected AS MATERIALIZED (
              SELECT * FROM products WHERE category_id=? AND active AND id>? ORDER BY id LIMIT ?
            ) SELECT p.id,p.sku,p.category_id,p.name,p.description,p.active,p.version,
                pr.price_minor,pr.currency,i.on_hand-i.reserved AS available
              FROM selected p JOIN product_prices pr ON pr.product_id=p.id
              JOIN inventory i ON i.product_id=p.id ORDER BY p.id
            """,
            CommerceService::product,category,after,Math.clamp(limit,1,100));
    }
    @Transactional
    public Map<String,Object> cart(long user) {
        db.queryForObject("SELECT version FROM carts WHERE user_id=? FOR UPDATE",Long.class,user);
        return cartView(user);
    }
    private Map<String,Object> cartView(long user) {
        var items=db.queryForList("""
            SELECT ci.product_id AS "productId",p.name,ci.quantity,pr.price_minor AS "priceMinor"
            FROM cart_items ci JOIN products p ON p.id=ci.product_id
            JOIN product_prices pr ON pr.product_id=p.id WHERE ci.user_id=? ORDER BY ci.product_id
            """,user);
        long total=items.stream().mapToLong(x -> ((Number)x.get("priceMinor")).longValue()*((Number)x.get("quantity")).longValue()).sum();
        return Map.of("version",db.queryForObject("SELECT version FROM carts WHERE user_id=?",Long.class,user),
            "items",items,"totalMinor",total,"currency","RUB");
    }
    @Transactional
    public Map<String,Object> setCart(long user,long product,int quantity) {
        db.queryForObject("SELECT version FROM carts WHERE user_id=? FOR UPDATE",Long.class,user);
        if (quantity==0) db.update("DELETE FROM cart_items WHERE user_id=? AND product_id=?",user,product);
        else {
            if (db.queryForObject("SELECT count(*) FROM products WHERE id=? AND active",Integer.class,product)!=1)
                throw new ApiFailure(404,"PRODUCT_NOT_FOUND");
            int count=db.queryForObject("SELECT count(*) FROM cart_items WHERE user_id=?",Integer.class,user);
            if (count>=50 && db.queryForObject("SELECT count(*) FROM cart_items WHERE user_id=? AND product_id=?",Integer.class,user,product)==0)
                throw new ApiFailure(400,"CART_LIMIT");
            db.update("INSERT INTO cart_items VALUES (?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET quantity=excluded.quantity",user,product,quantity);
        }
        db.update("UPDATE carts SET version=version+1,updated_at=now() WHERE user_id=?",user);
        return cartView(user);
    }
    private String encode(Object value) {
        try { return json.writeValueAsString(value); } catch (Exception error) { throw new IllegalStateException(error); }
    }
    private Map<String,Object> decode(String value) {
        try { return json.readValue(value,new TypeReference<>(){}); } catch (Exception error) { throw new IllegalStateException(error); }
    }
    private String hash(long version,long amount) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest((version+":"+amount).getBytes(StandardCharsets.UTF_8))); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }
    private Result finish(long user,String key,int status,Map<String,Object> body) {
        db.update("UPDATE idempotency_keys SET response_status=?,response_body=?::jsonb WHERE user_id=? AND key=?",status,encode(body),user,key);
        return new Result(status,body);
    }
    private Result reject(long user,String key,String code) { return finish(user,key,409,Map.of("code",code)); }
    private void changed(long product,long version) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { cache.invalidate(product,version); }
        });
    }
    private void event(String type,String aggregateType,Object aggregate,long version,Map<String,Object> body) {
        db.update("INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload) VALUES (?,?,?,?,?,?::jsonb)",
            UUID.randomUUID(),aggregateType,aggregate.toString(),version,type,encode(body));
    }
    private void inventoryChanged(long product) {
        long version=db.queryForObject("UPDATE products SET version=version+1 WHERE id=? RETURNING version",Long.class,product);
        event("InventoryChanged","Product",product,version,Map.of("productId",product,"version",version));
        changed(product,version);
    }
    @Transactional
    public Result checkout(long user,String key,long expectedVersion,long expectedTotal) {
        if (!key.matches("[A-Za-z0-9_-]{1,80}")) throw new ApiFailure(400,"INVALID_IDEMPOTENCY_KEY");
        String requestHash=hash(expectedVersion,expectedTotal);
        var durable=db.queryForList("SELECT request_hash,checkout_response::text FROM orders WHERE user_id=? AND idempotency_key=?",user,key);
        if (!durable.isEmpty()) {
            var old=durable.getFirst();
            if (!requestHash.equals(old.get("request_hash"))) throw new ApiFailure(409,"IDEMPOTENCY_CONFLICT");
            return new Result(201,decode((String)old.get("checkout_response")));
        }
        int inserted=db.update("INSERT INTO idempotency_keys(user_id,key,request_hash) VALUES (?,?,?) ON CONFLICT DO NOTHING",user,key,requestHash);
        if (inserted==0) {
            var old=db.queryForMap("SELECT request_hash,response_status,response_body::text FROM idempotency_keys WHERE user_id=? AND key=?",user,key);
            if (!requestHash.equals(old.get("request_hash"))) throw new ApiFailure(409,"IDEMPOTENCY_CONFLICT");
            return new Result(((Number)old.get("response_status")).intValue(),decode((String)old.get("response_body")));
        }
        long version=db.queryForObject("SELECT version FROM carts WHERE user_id=? FOR UPDATE",Long.class,user);
        if (version!=expectedVersion) return reject(user,key,"CART_CHANGED");
        List<Item> items=db.query("SELECT product_id,quantity FROM cart_items WHERE user_id=? ORDER BY product_id",(r,n) -> new Item(r.getLong(1),r.getInt(2)),user);
        if (items.isEmpty()) return reject(user,key,"EMPTY_CART");
        var ids=items.stream().map(Item::productId).toList();
        // Every writer locks products in the same order before inventory/price changes.
        var products=named.query("""
            SELECT p.id,p.sku,p.category_id,p.name,p.description,p.active,p.version,pr.price_minor,pr.currency,0 AS available
            FROM products p JOIN product_prices pr ON pr.product_id=p.id WHERE p.id IN (:ids)
            ORDER BY p.id FOR UPDATE OF p,pr
            """,Map.of("ids",ids),CommerceService::product);
        var stock=named.queryForList("SELECT product_id,on_hand-reserved AS available FROM inventory WHERE product_id IN (:ids) ORDER BY product_id FOR UPDATE",Map.of("ids",ids));
        Map<Long,Product> byId=new HashMap<>(); products.forEach(p -> byId.put(p.id(),p));
        Map<Long,Integer> available=new HashMap<>(); stock.forEach(r -> available.put(((Number)r.get("product_id")).longValue(),((Number)r.get("available")).intValue()));
        long total=0;
        for (var item:items) {
            var p=byId.get(item.productId());
            if (p==null || !p.active()) return reject(user,key,"PRODUCT_UNAVAILABLE");
            if (available.getOrDefault(item.productId(),0)<item.quantity()) return reject(user,key,"OUT_OF_STOCK");
            total=Math.addExact(total,Math.multiplyExact(p.priceMinor(),item.quantity()));
        }
        if (total!=expectedTotal) return reject(user,key,"PRICE_CHANGED");
        UUID order=UUID.randomUUID(),reservation=UUID.randomUUID();
        db.update("""
            INSERT INTO orders(id,user_id,status,total_minor,idempotency_key,request_hash,expires_at)
            VALUES (?,?,'PENDING_PAYMENT',?,?,?,clock_timestamp()+interval '15 minutes')
            """,order,user,total,key,requestHash);
        var expires=db.queryForObject("SELECT expires_at FROM orders WHERE id=?",OffsetDateTime.class,order);
        db.update("INSERT INTO reservations VALUES (?,?,'ACTIVE',?)",reservation,order,expires);
        for (var item:items) {
            Product p=byId.get(item.productId());
            db.update("UPDATE inventory SET reserved=reserved+? WHERE product_id=?",item.quantity(),item.productId());
            db.update("INSERT INTO order_items VALUES (?,?,?,?,?)",order,item.productId(),p.name(),p.priceMinor(),item.quantity());
            db.update("INSERT INTO reservation_items VALUES (?,?,?)",reservation,item.productId(),item.quantity());
            inventoryChanged(item.productId());
        }
        db.update("DELETE FROM cart_items WHERE user_id=?",user);
        db.update("UPDATE carts SET version=version+1,updated_at=now() WHERE user_id=?",user);
        Map<String,Object> body=Map.of("orderId",order.toString(),"status","PENDING_PAYMENT","totalMinor",total,"currency","RUB","expiresAt",expires.toString());
        db.update("UPDATE orders SET checkout_response=?::jsonb WHERE id=?",encode(body),order);
        event("OrderCreated","Order",order,1,body);
        return finish(user,key,201,body);
    }
    Map<String,Object> order(long user,UUID id) {
        var records=db.queryForList("SELECT id,status,total_minor AS \"totalMinor\",currency,created_at AS \"createdAt\",expires_at AS \"expiresAt\" FROM orders WHERE id=? AND user_id=?",id,user);
        if (records.isEmpty()) throw new ApiFailure(404,"ORDER_NOT_FOUND");
        var response=new LinkedHashMap<>(records.getFirst());
        response.put("items",db.queryForList("SELECT product_id AS \"productId\",name_snapshot AS name,price_minor AS \"priceMinor\",quantity FROM order_items WHERE order_id=? ORDER BY product_id",id));
        return response;
    }
    List<Map<String,Object>> orders(long user,OffsetDateTime before,UUID beforeId) {
        if ((before==null)!=(beforeId==null)) throw new ApiFailure(400,"INVALID_CURSOR");
        String cursor=before==null?"":" AND (created_at,id)<(?,?)";
        Object[] args=before==null?new Object[]{user}:new Object[]{user,before,beforeId};
        return db.queryForList("SELECT id,status,total_minor AS \"totalMinor\",created_at AS \"createdAt\" FROM orders WHERE user_id=?"+cursor+" ORDER BY created_at DESC,id DESC LIMIT 20",args);
    }
    @Transactional
    public Product updateProduct(long id,String name,long price,boolean active) {
        if (db.queryForList("SELECT id FROM products WHERE id=? FOR UPDATE",id).isEmpty()) throw new ApiFailure(404,"PRODUCT_NOT_FOUND");
        long version=db.queryForObject("UPDATE products SET name=?,active=?,version=version+1 WHERE id=? RETURNING version",Long.class,name,active,id);
        db.update("UPDATE product_prices SET price_minor=? WHERE product_id=?",price,id);
        event("ProductUpdated","Product",id,version,Map.of("productId",id,"version",version));
        changed(id,version);
        return db.query(PRODUCT+" WHERE p.id=?",CommerceService::product,id).getFirst();
    }
    @Transactional
    public Map<String,Object> updateStock(long id,int onHand) {
        if (db.queryForList("SELECT id FROM products WHERE id=? FOR UPDATE",id).isEmpty()) throw new ApiFailure(404,"PRODUCT_NOT_FOUND");
        if (db.update("UPDATE inventory SET on_hand=? WHERE product_id=? AND reserved<=?",onHand,id,onHand)!=1) throw new ApiFailure(409,"STOCK_BELOW_RESERVED");
        inventoryChanged(id);
        return db.queryForMap("SELECT on_hand,reserved FROM inventory WHERE product_id=?",id);
    }
    @Transactional
    public Map<String,Integer> expire() {
        var orders=db.queryForList("SELECT id FROM orders WHERE status='PENDING_PAYMENT' AND expires_at<now() ORDER BY expires_at,id LIMIT 100 FOR UPDATE SKIP LOCKED");
        for (var row:orders) {
            UUID id=(UUID)row.get("id");
            UUID reservation=db.queryForObject("SELECT id FROM reservations WHERE order_id=? AND state='ACTIVE' FOR UPDATE",UUID.class,id);
            var items=db.query("SELECT product_id,quantity FROM reservation_items WHERE reservation_id=? ORDER BY product_id",(r,n) -> new Item(r.getLong(1),r.getInt(2)),reservation);
            var ids=items.stream().map(Item::productId).toList();
            named.queryForList("SELECT id FROM products WHERE id IN (:ids) ORDER BY id FOR UPDATE",Map.of("ids",ids));
            for (var item:items) {
                db.update("UPDATE inventory SET reserved=reserved-? WHERE product_id=?",item.quantity(),item.productId());
                inventoryChanged(item.productId());
            }
            db.update("UPDATE reservations SET state='RELEASED' WHERE id=?",reservation);
            long version=db.queryForObject("UPDATE orders SET status='EXPIRED',version=version+1 WHERE id=? RETURNING version",Long.class,id);
            event("OrderExpired","Order",id,version,Map.of("orderId",id.toString()));
        }
        return Map.of("expired",orders.size());
    }
}
