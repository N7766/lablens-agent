package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.data.redis.core.script.RedisScript;

class RedisFailureTest {
    @Test void rateLimitFailureIsUnavailableRatherThanAllowedOrRateLimited() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        when(client.execute(any(RedisScript.class),anyList())).thenThrow(new RedisConnectionFailureException("offline"));
        var metrics=new SimpleMeterRegistry();
        var support=new RedisSupport(client,metrics,1,300,"test");
        assertEquals(RedisSupport.AdmissionDecision.UNAVAILABLE,support.allowSubmission("workspace"));
        assertEquals(1,metrics.get("lablens.redis.failures").tag("operation","rate_limit").counter().count());
        assertEquals(1,metrics.get("lablens.rate_limit.unavailable").counter().count());
        assertNull(metrics.find("lablens.rate_limit.rejected").counter());
    }
    @Test void nullScriptResultAlsoFailsTheAdmissionGate() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        var metrics=new SimpleMeterRegistry();
        var support=new RedisSupport(client,metrics,1,300,"test");
        assertEquals(RedisSupport.AdmissionDecision.UNAVAILABLE,support.allowSubmission("workspace"));
        assertEquals(1,metrics.get("lablens.redis.failures").tag("operation","rate_limit").counter().count());
        assertNull(metrics.find("lablens.rate_limit.rejected").counter());
    }
    @Test void availableLimiterUsesAnIsolatedWorkspaceCounterAndDistinguishesCapacity() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        when(client.execute(any(RedisScript.class),anyList())).thenReturn(1L,2L);
        var metrics=new SimpleMeterRegistry();
        var support=new RedisSupport(client,metrics,1,300,"test");
        assertEquals(RedisSupport.AdmissionDecision.ALLOWED,support.allowSubmission("workspace"));
        assertEquals(RedisSupport.AdmissionDecision.LIMITED,support.allowSubmission("workspace"));
        verify(client,times(2)).execute(any(RedisScript.class),eq(List.of("lablens:test:rate:workspace")));
        assertEquals(1,metrics.get("lablens.rate_limit.allowed").counter().count());
        assertEquals(1,metrics.get("lablens.rate_limit.rejected").counter().count());
    }
    @Test void explicitDisableDoesNotContactRedisAndIsVisibleEvenDuringOutage() {
        for (int limit:new int[]{0,-1}) {
            StringRedisTemplate client=mock(StringRedisTemplate.class);
            var support=new RedisSupport(client,new SimpleMeterRegistry(),limit,300,"test");
            assertEquals(RedisSupport.AdmissionDecision.ALLOWED,support.allowSubmission("workspace"));
            verifyNoInteractions(client);
            when(client.execute(any(RedisCallback.class))).thenThrow(new RedisConnectionFailureException("offline"));
            var status=support.status();
            assertEquals(false,status.get("available"));
            assertEquals(false,status.get("rate_limit_enabled"));
            assertEquals("disabled_by_configuration",status.get("admission_policy"));
        }
    }
    @Test void unknownPingIsNotReportedAsHealthyAndPoliciesSeparateCacheFromProviderAdmission() {
        StringRedisTemplate client=mock(StringRedisTemplate.class);
        var support=new RedisSupport(client,new SimpleMeterRegistry(),1,300,"test");
        var unknown=support.status();
        assertEquals(false,unknown.get("available"));
        assertEquals(true,unknown.get("degraded"));
        assertEquals(true,unknown.get("rate_limit_enabled"));
        assertEquals("fail_open",unknown.get("cache_policy"));
        assertEquals("provider_fail_closed; offline_bm25_extractive_uses_mysql_guard",unknown.get("admission_policy"));
        when(client.execute(any(RedisCallback.class))).thenReturn("PONG");
        assertEquals(true,support.status().get("available"));
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
