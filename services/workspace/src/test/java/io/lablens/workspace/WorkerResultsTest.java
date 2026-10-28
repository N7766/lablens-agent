package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class WorkerResultsTest {
    private final WorkspaceStore.Claimed task=new WorkspaceStore.Claimed("t","w",7,"hash","extractive","{}");
    private final Map<String,Object> snapshot=Map.of("strategy","hybrid");
    private Map<String,Object> result() {
        var r=new LinkedHashMap<String,Object>();
        r.put("status","excerpts"); r.put("mode","extractive");
        r.put("retrieval",Map.of("snapshot_id","w:7","strategy","hybrid"));
        var usage=WorkerResults.zeroUsage(false); usage.put("embedding_requests",2); usage.put("embedded_inputs",4); usage.put("embedding_tokens",123);
        r.put("usage",usage); return r;
    }
    @Test void emptyOrErrorStatus200CannotBecomeSuccess() {
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(Map.of(),task,snapshot));
        var r=result(); r.put("status","provider_error");
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(r,task,snapshot));
    }
    @Test void resultMustMatchImmutableSnapshotModeAndStrategy() {
        var mode=result(); mode.put("mode","live");
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(mode,task,snapshot));
        var revision=result(); revision.put("retrieval",Map.of("snapshot_id","w:8","strategy","hybrid"));
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(revision,task,snapshot));
        var strategy=result(); strategy.put("retrieval",Map.of("snapshot_id","w:7","strategy","bm25"));
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(strategy,task,snapshot));
    }
    @Test void usageMustExistAndContainSafeNonnegativeCounters() {
        var missing=result(); missing.remove("usage");
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(missing,task,snapshot));
        var invalid=WorkerResults.zeroUsage(false); invalid.put("embedding_requests",-1);
        assertThrows(IllegalArgumentException.class,()->WorkerResults.usage(invalid));
        invalid.put("embedding_requests",1.5);
        assertThrows(IllegalArgumentException.class,()->WorkerResults.usage(invalid));
    }
    @Test void cacheHitHasZeroCurrentUsageAndSeparatelyLabelledOrigin() {
        var original=result(); var origin=WorkerResults.validate(original,task,snapshot);
        var cached=WorkerResults.cachedResult(original,origin);
        assertEquals(WorkerResults.zeroUsage(true),cached.get("usage"));
        assertEquals(origin,cached.get("origin_usage"));
        assertEquals(2,((Map<?,?>)original.get("usage")).get("embedding_requests"));
        assertFalse(original.containsKey("origin_usage"));
    }
    @Test void extractiveDenseAndHybridCanIncurProviderUsage() {
        assertTrue(WorkerResults.canUseProvider(task,Map.of("strategy","dense")));
        assertTrue(WorkerResults.canUseProvider(task,Map.of("strategy","hybrid")));
        assertFalse(WorkerResults.canUseProvider(task,Map.of("strategy","bm25")));
        assertTrue(WorkerResults.canUseProvider(new WorkspaceStore.Claimed("t","w",7,"h","live","{}"),Map.of("strategy","bm25")));
        assertNull(WorkerResults.uncertainUsage(true).get("embedding_requests"));
        assertEquals(true,WorkerResults.uncertainUsage(true).get("provider_usage_unknown"));
    }
    @Test void errorUsagePreservedWithoutEchoingArbitraryProviderTextOrFields() {
        var error=result(); error.put("status","provider_error"); error.put("error","secret-token-do-not-copy");
        var usage=WorkerResults.zeroUsage(false); usage.put("embedding_requests",2); usage.put("embedding_tokens",null); usage.put("provider_usage_unknown",true); usage.put("secret","secret-token-do-not-copy");
        error.put("usage",usage);
        var safe=WorkerResults.safeError(Map.of("detail",error),task,snapshot);
        assertNotNull(safe); assertFalse(safe.toString().contains("secret-token"));
        assertEquals(2,((Map<?,?>)safe.get("usage")).get("embedding_requests"));
        assertEquals(true,((Map<?,?>)safe.get("usage")).get("provider_usage_unknown"));
    }
    @Test void mismatchedOrUnstructuredErrorsCannotOverrideUnknownUsage() {
        assertNull(WorkerResults.safeError(Map.of("detail","error"),task,snapshot));
        var error=result(); error.put("status","provider_error"); error.put("mode","live");
        assertNull(WorkerResults.safeError(Map.of("detail",error),task,snapshot));
        error.put("mode","extractive"); error.put("usage",Map.of());
        assertNull(WorkerResults.safeError(Map.of("detail",error),task,snapshot));
    }
    @Test void missingTokenReportsRemainUnknownDespiteFalseProviderFlag() {
        var usage=WorkerResults.zeroUsage(false); usage.put("embedding_tokens",null);
        assertEquals(true,WorkerResults.usage(usage).get("provider_usage_unknown"));
    }
    @Test void agentBm25StillUsesProviderAndKeepsAssessmentResult() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        assertTrue(WorkerResults.canUseProvider(agent,Map.of("strategy","bm25")));
        var result=result(); result.put("mode","agent"); result.put("status","answered");
        result.put("assessments",java.util.List.of(Map.of("verdict","contradicted")));
        result.put("evidence_verdict","contradicted");
        WorkerResults.validate(result,agent,snapshot);
        assertEquals("contradicted",result.get("evidence_verdict"));
        assertEquals(java.util.List.of(Map.of("verdict","contradicted")),result.get("assessments"));
    }
    @Test void boundedAgentFailureTraceIsPersistedOnlyForMatchingSnapshot() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var error=result(); error.put("mode","agent"); error.put("status","agent_timeout");
        error.put("error","secret-provider-error-never-copy");
        var trace=new LinkedHashMap<String,Object>();
        trace.put("version","workspace-agent-v1"); trace.put("snapshot_id","w:7"); trace.put("state","timeout");
        trace.put("trace",java.util.List.of(Map.of("event","model_decision","step",1,"tools",java.util.List.of("search_workspace"))));
        error.put("agent",trace);
        var safe=WorkerResults.safeError(Map.of("detail",error),agent,snapshot);
        assertNotNull(safe); assertTrue(safe.containsKey("agent"));
        assertFalse(safe.toString().contains("secret-provider-error"));
        trace.put("snapshot_id","wrong:1");
        assertFalse(WorkerResults.safeError(Map.of("detail",error),agent,snapshot).containsKey("agent"));
    }
    @Test void partialFailureUsageKeepsKnownModelCallsAndUnknownEmbeddingCountsButSuccessStaysStrict() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var error=result(); error.put("mode","agent"); error.put("status","agent_timeout");
        var usage=WorkerResults.zeroUsage(false); usage.put("model_calls",1); usage.put("provider_usage_unknown",true);
        for (String field:java.util.List.of("embedding_requests","embedded_inputs","embedding_cache_hits","embedding_tokens")) usage.put(field,null);
        error.put("usage",usage);
        var saved=WorkerResults.safeError(Map.of("detail",error),agent,snapshot);
        assertNotNull(saved); var persisted=(Map<?,?>)saved.get("usage");
        assertEquals(1,persisted.get("model_calls")); assertNull(persisted.get("embedding_requests"));
        assertEquals(true,persisted.get("provider_usage_unknown"));
        error.put("status","answered");
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(error,agent,snapshot));
        usage.put("model_calls",null); error.put("status","agent_timeout");
        assertNull(WorkerResults.safeError(Map.of("detail",error),agent,snapshot));
    }

}
