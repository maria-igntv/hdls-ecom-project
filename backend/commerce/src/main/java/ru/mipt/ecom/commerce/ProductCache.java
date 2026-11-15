package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
class ProductCache {
    private static final DefaultRedisScript<String> GET = new DefaultRedisScript<>("""
        local expiry=redis.call('HGET',KEYS[1],'expires')
        if not expiry or tonumber(expiry)<=tonumber(ARGV[1]) then return nil end
        return redis.call('HGET',KEYS[1],'body')
        """,String.class);
    private static final DefaultRedisScript<Long> PUT = new DefaultRedisScript<>("""
        local current=tonumber(redis.call('HGET',KEYS[1],'version') or '0')
        if tonumber(ARGV[1])<current then return 0 end
        redis.call('HSET',KEYS[1],'version',ARGV[1],'expires',ARGV[2],'body',ARGV[3])
        redis.call('EXPIRE',KEYS[1],86400)
        return 1
        """,Long.class);
    private static final DefaultRedisScript<Long> INVALIDATE = new DefaultRedisScript<>("""
        local current=tonumber(redis.call('HGET',KEYS[1],'version') or '0')
        if tonumber(ARGV[1])<current then return 0 end
        redis.call('HSET',KEYS[1],'version',ARGV[1],'expires','0')
        redis.call('HDEL',KEYS[1],'body')
        redis.call('EXPIRE',KEYS[1],86400)
        return 1
        """,Long.class);
    private final StringRedisTemplate redis;
    private final ObjectMapper json;
    private final ConcurrentHashMap<Long,CompletableFuture<CommerceService.Product>> flights = new ConcurrentHashMap<>();
    private final Semaphore originSlots = new Semaphore(4);
    private final AtomicLong loads=new AtomicLong(),hits=new AtomicLong(),errors=new AtomicLong();
    private volatile long retryAfter;
    ProductCache(StringRedisTemplate redis,ObjectMapper json) { this.redis=redis; this.json=json; }
    private String key(long id) { return "product:"+id; }
    private <T> T access(Supplier<T> operation) {
        if (System.nanoTime()<retryAfter) return null;
        try { return operation.get(); }
        catch (DataAccessException failure) { errors.incrementAndGet(); retryAfter=System.nanoTime()+Duration.ofSeconds(1).toNanos(); return null; }
    }
    CommerceService.Product get(long id,Supplier<CommerceService.Product> loader) {
        String cached=access(() -> redis.execute(GET,List.of(key(id)),Long.toString(System.currentTimeMillis())));
        if (cached!=null) {
            try { var product=json.readValue(cached,CommerceService.Product.class); hits.incrementAndGet(); return product; }
            catch (java.io.IOException ignored) { errors.incrementAndGet(); }
        }
        var own=new CompletableFuture<CommerceService.Product>();
        var existing=flights.putIfAbsent(id,own);
        if (existing!=null) {
            try { return existing.get(12,TimeUnit.SECONDS); }
            catch (ExecutionException failure) {
                if (failure.getCause() instanceof RuntimeException cause) throw cause;
                throw new CommerceService.ApiFailure(503,"CATALOG_BUSY");
            }
            catch (InterruptedException failure) { Thread.currentThread().interrupt(); throw new CommerceService.ApiFailure(503,"CATALOG_BUSY"); }
            catch (TimeoutException failure) { throw new CommerceService.ApiFailure(503,"CATALOG_BUSY"); }
        }
        boolean acquired=false;
        try {
            acquired=originSlots.tryAcquire(100,TimeUnit.MILLISECONDS);
            if (!acquired) throw new CommerceService.ApiFailure(503,"CATALOG_BUSY");
            loads.incrementAndGet();
            var product=loader.get();
            String body=json.writeValueAsString(product);
            long expires=System.currentTimeMillis()+ThreadLocalRandom.current().nextLong(60000,90001);
            access(() -> redis.execute(PUT,List.of(key(id)),Long.toString(product.version()),Long.toString(expires),body));
            own.complete(product);
            return product;
        } catch (RuntimeException failure) { own.completeExceptionally(failure); throw failure; }
        catch (Exception failure) { own.completeExceptionally(failure); throw new CommerceService.ApiFailure(503,"CATALOG_BUSY"); }
        finally { if (acquired) originSlots.release(); flights.remove(id,own); }
    }
    void invalidate(long id,long version) { access(() -> redis.execute(INVALIDATE,List.of(key(id)),Long.toString(version))); }
    Map<String,Long> stats() { return Map.of("originLoads",loads.get(),"hits",hits.get(),"redisErrors",errors.get()); }
}
