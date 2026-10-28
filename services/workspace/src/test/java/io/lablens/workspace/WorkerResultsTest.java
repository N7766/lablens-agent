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
}
