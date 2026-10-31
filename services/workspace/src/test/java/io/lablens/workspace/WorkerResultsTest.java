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
    private Map<String,Object> timingFailure(java.util.List<?> events) {
        var error=result();error.put("mode","agent");error.put("status","agent_timeout");
        error.put("agent",Map.of("version","workspace-agent-v5","snapshot_id","w:7","state","timeout",
            "telemetry_version","runtime-stages-v1","trace",events));
        return WorkerResults.safeError(Map.of("detail",error),new WorkspaceStore.Claimed("t","w",7,"h","agent","{}"),snapshot);
    }
    private Map<String,Object> reviewTimeout() {
        var event=new LinkedHashMap<String,Object>();
        event.put("event","request_finished");event.put("phase","review");event.put("stage_id","s3");event.put("request_id","r2");
        event.put("step",2);event.put("elapsed_ms",75000.0);event.put("remaining_ms",0.0);event.put("duration_ms",20000.0);
        event.put("request_body_bytes",8500);event.put("timeout_ms",20000.0);event.put("outcome","timeout");
        event.put("response_received",false);event.put("http_status",null);event.put("usage_known",false);event.put("provider_acceptance","unknown");
        var tokens=new LinkedHashMap<String,Object>();tokens.put("prompt_tokens",null);tokens.put("completion_tokens",null);tokens.put("total_tokens",null);
        event.put("usage",tokens);return event;
    }
    @Test void timedOutReviewDiagnosticsPersistWithoutPayloadOrProviderSecrets() {
        var event=reviewTimeout();event.put("headers",Map.of("Authorization","secret-not-to-copy"));event.put("payload","private prompt not to copy");
        event.put("exception","secret-not-to-copy");
        var saved=timingFailure(java.util.List.of(event));assertNotNull(saved);
        var agent=(Map<?,?>)saved.get("agent");assertEquals("runtime-stages-v1",agent.get("telemetry_version"));
        var clean=(Map<?,?>)((java.util.List<?>)agent.get("trace")).getFirst();
        assertEquals(20000.0,clean.get("duration_ms"));assertEquals(8500,clean.get("request_body_bytes"));
        assertEquals(false,clean.get("usage_known"));assertEquals("unknown",clean.get("provider_acceptance"));
        assertNull(((Map<?,?>)clean.get("usage")).get("total_tokens"));
        assertFalse(saved.toString().contains("not-to-copy"));assertFalse(saved.toString().contains("private prompt"));
    }
    @Test void v6FailureRetainsVisibilityAndReadGuardAlongsideUnknownProviderUsage() {
        var error=result(); error.put("mode","agent"); error.put("status","agent_timeout");
        var read=Map.of("event","tool","name","read_source_passages","output",Map.of(
            "status","already_visible","already_visible_chunk_ids",java.util.List.of("c1"),
            "restored_chunk_ids",java.util.List.of(),"passages",Map.of()));
        error.put("agent",Map.of("version","workspace-agent-v6","snapshot_id","w:7","state","timeout",
            "telemetry_version","runtime-stages-v1","trace",java.util.List.of(read,reviewTimeout())));
        var visibility=Map.of("phase","decision_3","evidence_projection","visible-exact-passages-v1",
            "visible_chunk_ids",java.util.List.of("c1"),"readable_chunk_ids",java.util.List.of(),
            "read_action_available",false,"deduplicated_passage_count",2);
        error.put("context",Map.of("context_budget",Map.of("calls",java.util.List.of(visibility))));
        var saved=WorkerResults.safeError(Map.of("detail",error),new WorkspaceStore.Claimed("t","w",7,"h","agent","{}"),snapshot);
        assertNotNull(saved);
        var agent=(Map<?,?>)saved.get("agent"); assertEquals("workspace-agent-v6",agent.get("version"));
        var trace=(java.util.List<?>)agent.get("trace"); assertEquals(read,trace.getFirst());
        assertEquals(false,((Map<?,?>)trace.getLast()).get("usage_known"));
        var budget=(Map<?,?>)((Map<?,?>)saved.get("context")).get("context_budget");
        assertEquals(java.util.List.of(visibility),budget.get("calls"));
    }
    @Test void malformedTimingCannotTurnIntoAFalseFiniteMeasurement() {
        for(Object bad:java.util.List.of(-1.0,Double.NaN,Double.POSITIVE_INFINITY,"10")) {
            var event=reviewTimeout();event.put("duration_ms",bad);
            var saved=timingFailure(java.util.List.of(event));assertNotNull(saved);assertFalse(saved.containsKey("agent"));
            assertTrue(saved.containsKey("usage"));
        }
        var event=reviewTimeout();event.put("phase","private-secret-phase");
        assertFalse(timingFailure(java.util.List.of(event)).containsKey("agent"));
    }
    @Test void missingTokenValuesOverrideAnIncorrectKnownFlag() {
        var event=reviewTimeout();event.put("usage_known",true);event.put("response_received",true);event.put("provider_acceptance","response_received");
        var saved=timingFailure(java.util.List.of(event));
        var clean=(Map<?,?>)((java.util.List<?>)((Map<?,?>)saved.get("agent")).get("trace")).getFirst();
        assertEquals(false,clean.get("usage_known"));assertEquals(true,clean.get("response_received"));
        assertNull(((Map<?,?>)clean.get("usage")).get("total_tokens"));
    }
    @Test void nestedToolAndReviewStagesKeepTheirParentAndBoundedTrace() {
        var start=new LinkedHashMap<String,Object>();start.put("event","stage_started");start.put("phase","review");start.put("stage_id","s3");
        start.put("parent_stage_id","s2");start.put("elapsed_ms",45000.0);start.put("remaining_ms",30000.0);
        var saved=timingFailure(java.util.List.of(start,reviewTimeout()));
        var clean=(Map<?,?>)((java.util.List<?>)((Map<?,?>)saved.get("agent")).get("trace")).getFirst();assertEquals("s2",clean.get("parent_stage_id"));
        var events=new java.util.ArrayList<Map<String,Object>>();
        for(int i=0;i<128;i++)events.add(Map.of("event","stopped","state","timeout"));
        assertTrue(timingFailure(events).containsKey("agent"));events.add(Map.of("event","stopped","state","timeout"));
        assertFalse(timingFailure(events).containsKey("agent"));
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
    @Test void rejectedDecisionMetadataSurvivesFailureWithoutRawModelContent() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var error=result(); error.put("mode","agent"); error.put("status","agent_protocol_error");
        var diagnostic=Map.of("event","model_decision_rejected","step",3,"output",
            Map.of("finish_reason","stop","has_tool_calls",false,"tool_call_count",0,"has_content",true));
        error.put("agent",Map.of("version","workspace-agent-v2","snapshot_id","w:7","state","protocol_error","trace",java.util.List.of(diagnostic)));
        var safe=WorkerResults.safeError(Map.of("detail",error),agent,snapshot);
        assertNotNull(safe);
        assertEquals(java.util.List.of(diagnostic),((Map<?,?>)safe.get("agent")).get("trace"));
    }
    @Test void clarificationIsAnAgentTerminalSuccessWithMatchingConversationIdentity() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var contextual=Map.<String,Object>of("strategy","hybrid","conversation_context",Map.of("id","c","revision",3));
        var r=result(); r.put("mode","agent"); r.put("status","clarification_required");
        r.put("clarification",Map.of("question","Which model?","choices",java.util.List.of("SFT","DPO")));
        r.put("context",Map.of("conversation_id","c","conversation_revision",3));
        WorkerResults.validate(r,agent,contextual);
        r.put("context",Map.of("conversation_id","different","conversation_revision",3));
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(r,agent,contextual));
        r.remove("context"); assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(r,agent,contextual));
        r.put("mode","extractive"); assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(r,task,snapshot));
    }
    @Test void v4QualityDiagnosticsSurviveFailureWithoutAcceptingAnUnknownEvent() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var error=result(); error.put("mode","agent"); error.put("status","agent_timeout");
        var contract=Map.of("event","answer_contract","output",Map.of("requires_clarification",false));
        var quality=Map.of("event","evidence_quality","output",Map.of("semantic_entailment_verified",false,"findings",java.util.List.of("unquoted_value")));
        var trace=new LinkedHashMap<String,Object>();
        trace.put("version","workspace-agent-v4"); trace.put("snapshot_id","w:7"); trace.put("state","timeout");
        trace.put("trace",java.util.List.of(contract,quality));
        error.put("agent",trace);
        var saved=WorkerResults.safeError(Map.of("detail",error),agent,snapshot);
        assertNotNull(saved);
        assertEquals(java.util.List.of(contract,quality),((Map<?,?>)saved.get("agent")).get("trace"));
        trace.put("trace",java.util.List.of(Map.of("event","unrecognised_private_event")));
        assertFalse(WorkerResults.safeError(Map.of("detail",error),agent,snapshot).containsKey("agent"));
    }
    @Test void contextBudgetFailureKeepsIdentityBudgetAndV2TraceButRejectsAnotherConversation() {
        var agent=new WorkspaceStore.Claimed("t","w",7,"h","agent","{}");
        var contextual=Map.<String,Object>of("strategy","hybrid","conversation_context",Map.of("id","c","revision",3));
        var error=result(); error.put("mode","agent"); error.put("status","context_budget_exceeded");
        var context=Map.of("conversation_id","c","conversation_revision",3,"context_budget",Map.of("window_units",1024,"calls",java.util.List.of(Map.of("fits",false))),"provider_secret","do-not-copy");
        error.put("context",context);
        error.put("agent",Map.of("version","workspace-agent-v2","snapshot_id","w:7","state","context_budget_exceeded","trace",java.util.List.of(Map.of("event","stopped","state","context_budget_exceeded"))));
        var saved=WorkerResults.safeError(Map.of("detail",error),agent,contextual);
        assertNotNull(saved); assertTrue(saved.containsKey("agent")); assertFalse(saved.toString().contains("do-not-copy"));
        Map<?,?> metadata=(Map<?,?>)saved.get("context"); assertEquals("c",metadata.get("conversation_id"));
        assertEquals(context.get("context_budget"),metadata.get("context_budget"));
        error.put("context",Map.of("conversation_id","c","conversation_revision",2));
        assertNull(WorkerResults.safeError(Map.of("detail",error),agent,contextual));
    }

}
