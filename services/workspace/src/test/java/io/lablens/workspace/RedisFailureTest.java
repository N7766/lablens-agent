package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.data.redis.core.script.RedisScript;

class RedisFailureTest {
    @Test void rateLimitFailureOpensOnlyRedisGateAndRecordsDegradation() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        when(client.execute(any(RedisScript.class),anyList())).thenThrow(new RedisConnectionFailureException("offline"));
        var metrics=new SimpleMeterRegistry();
        var support=new RedisSupport(client,metrics,1,300,"test");
        assertTrue(support.allowSubmission("workspace"));
        assertEquals(1,metrics.get("lablens.redis.failures").tag("operation","rate_limit").counter().count());
    }
    @Test void cacheOutageIsAMissAndLiveResultsNeverTouchRedisCache() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        ValueOperations<String,String> ops=mock(ValueOperations.class);
        when(client.opsForValue()).thenReturn(ops);
        when(ops.get(anyString())).thenThrow(new RedisConnectionFailureException("offline"));
        var metrics=new SimpleMeterRegistry();
        var support=new RedisSupport(client,metrics,100,300,"test");
        assertNull(support.get(task("extractive")));
        assertEquals(1,metrics.get("lablens.redis.failures").tag("operation","cache_read").counter().count());
        clearInvocations(client,ops);
        assertNull(support.get(task("live"))); support.put(task("live"),"{}");
        assertNull(support.get(task("agent"))); support.put(task("agent"),"{}");
        verifyNoInteractions(client,ops);
    }
    private WorkspaceStore.Claimed task(String mode) { return new WorkspaceStore.Claimed("task","workspace",1,"hash",mode,"{}"); }
}
