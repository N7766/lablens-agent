package io.lablens.workspace;

import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

/** Redis accelerates requests; MySQL remains authoritative when Redis is unavailable. */
@Service
public class RedisSupport {
    private static final DefaultRedisScript<Long> LIMIT=new DefaultRedisScript<>("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",Long.class);
    private final StringRedisTemplate redis;
    private final MeterRegistry metrics;
    private final int limit;
    private final Duration ttl;
    private final String namespace;
    private final AtomicBoolean available=new AtomicBoolean(true);
    private final AtomicLong failures=new AtomicLong();
    public RedisSupport(StringRedisTemplate redis,MeterRegistry metrics,@Value("${lablens.rate-limit}") int limit,
                        @Value("${lablens.cache-ttl-seconds}") int ttl,@Value("${lablens.cache-namespace}") String namespace) {
        this.redis=redis; this.metrics=metrics; this.limit=limit; this.ttl=Duration.ofSeconds(ttl); this.namespace=namespace;
    }
    public boolean allowSubmission(String workspace) {
        if (limit<=0) return true;
        try {
            Long count=redis.execute(LIMIT,List.of("lablens:"+namespace+":rate:"+workspace));
            available.set(true);
            boolean allowed=count!=null && count<=limit;
            metrics.counter("lablens.rate_limit."+(allowed?"allowed":"rejected")).increment();
            return allowed;
        } catch (RuntimeException e) {
            degraded("rate_limit"); return true;
        }
    }
    String key(WorkspaceStore.Claimed task) {
        return "lablens:"+namespace+":result:"+task.workspace()+":"+task.revision()+":"+task.requestHash();
    }
    String get(WorkspaceStore.Claimed task) {
        if (!"extractive".equals(task.mode())) return null;
        try {
            String result=redis.opsForValue().get(key(task));
            available.set(true); metrics.counter("lablens.cache."+(result==null?"misses":"lookup_hits")).increment();
            return result;
        } catch (RuntimeException e) { degraded("cache_read"); return null; }
    }
    void put(WorkspaceStore.Claimed task,String result) {
        if (!"extractive".equals(task.mode())) return;
        try { redis.opsForValue().set(key(task),result,ttl); available.set(true); metrics.counter("lablens.cache.writes").increment(); }
        catch (RuntimeException e) { degraded("cache_write"); }
    }
    void validatedHit() { metrics.counter("lablens.cache.hits").increment(); }
    void invalidate(WorkspaceStore.Claimed task) {
        metrics.counter("lablens.cache.invalid_entries").increment();
        try { redis.delete(key(task)); available.set(true); }
        catch (RuntimeException e) { degraded("cache_invalidate"); }
    }
    private void degraded(String operation) {
        available.set(false); failures.incrementAndGet(); metrics.counter("lablens.redis.failures","operation",operation).increment();
    }
    public Map<String,Object> status() {
        try { redis.execute((org.springframework.data.redis.core.RedisCallback<String>) connection->connection.ping()); available.set(true); }
        catch (RuntimeException e) { available.set(false); }
        return Map.of("available",available.get(),"degraded",!available.get(),"failure_count",failures.get(),"policy","fail_open_cache_and_submission_limit; MySQL idempotency remains authoritative","rate_limit_per_minute",limit,"cache_ttl_seconds",ttl.toSeconds());
    }
}
