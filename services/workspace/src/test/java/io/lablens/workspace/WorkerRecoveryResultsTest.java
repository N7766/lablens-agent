package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.Test;

class WorkerRecoveryResultsTest {
    final WorkspaceStore.Claimed task=new WorkspaceStore.Claimed("child","w",1,"hash","agent","{}");
    Map<String,Object> amount(int calls,int tokens) {
        var usage=WorkerResults.zeroUsage(false);usage.put("model_calls",calls);
        usage.put("generation_tokens",Map.of("prompt_tokens",tokens,"completion_tokens",tokens,"total_tokens",2*tokens));return usage;
    }
    Map<String,Object> snapshot(Map<String,Object> inherited) {
        var payload=Map.of("next_step",2,"elapsed_ms",150,"result",Map.of("usage",inherited,"agent",Map.of("tool_calls",1)));
        String raw=MemoryRequests.canonical(payload);
        return Map.of("strategy","bm25","resume_context",Map.of("parent_task_id","parent","checkpoint_revision",3,"state",Map.of("payload_json",raw),"state_sha256",Requests.sha256(raw)));
    }
    Map<String,Object> result(Map<String,Object> snapshot,Map<String,Object> usage) {
        var expected=(Map<?,?>)snapshot.get("resume_context");
        var r=new LinkedHashMap<String,Object>();r.put("mode","agent");r.put("status","answered");r.put("retrieval",Map.of("snapshot_id","w:1","strategy","bm25"));r.put("usage",usage);
        r.put("recovery",new LinkedHashMap<>(Map.of("parent_task_id","parent","checkpoint_revision",3,"state_sha256",expected.get("state_sha256"),"restored_next_step",2,"restored_elapsed_ms",150,"restored_model_calls",1,"restored_tool_calls",1,"downtime_charged",false)));
        r.put("usage_scope","cumulative_original_and_resumed_execution");
        return r;
    }
    @Test void persistedExecutionUsageChargesOnlyNewCallsWhileResultRetainsCumulativeUsage() {
        var snapshot=snapshot(amount(1,10));var cumulative=amount(3,40);var result=result(snapshot,cumulative);
        var validated=WorkerResults.validate(result,task,snapshot);var execution=WorkerResults.executionUsage(validated,result,snapshot);
        assertEquals(2L,execution.get("model_calls"));assertEquals(60L,((Map<?,?>)execution.get("generation_tokens")).get("total_tokens"));
        assertEquals(3,cumulative.get("model_calls"));assertEquals(cumulative,WorkerResults.continuedResult(result,snapshot).get("usage"));
    }
    @Test void unknownInheritedTokensCannotBecomeKnownBySubtractingFromLaterTotals() {
        var inherited=amount(1,10);var tokens=new LinkedHashMap<String,Object>();tokens.put("prompt_tokens",null);tokens.put("completion_tokens",null);tokens.put("total_tokens",null);
        inherited.put("generation_tokens",tokens);inherited.put("provider_usage_unknown",true);
        var snapshot=snapshot(inherited);var result=result(snapshot,amount(3,40));var delta=WorkerResults.executionUsage(amount(3,40),result,snapshot);
        assertEquals(2L,delta.get("model_calls"));assertNull(((Map<?,?>)delta.get("generation_tokens")).get("total_tokens"));assertEquals(true,delta.get("provider_usage_unknown"));
    }
    @Test void mismatchedRestoredProvenanceAndDecreasingCountersAreRejected() {
        var snapshot=snapshot(amount(1,10));var result=result(snapshot,amount(3,40));
        ((Map<String,Object>)result.get("recovery")).put("restored_model_calls",0);assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(result,task,snapshot));
        var decreasing=result(snapshot,amount(0,0));assertThrows(IllegalArgumentException.class,()->WorkerResults.executionUsage(amount(0,0),decreasing,snapshot));
        var missing=result(snapshot,amount(3,40));missing.remove("recovery");assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(missing,task,snapshot));
    }
    @Test void preflightFailureWithoutRestorationKeepsItsZeroLocalUsage() {
        var snapshot=snapshot(amount(1,10));var result=result(snapshot,WorkerResults.zeroUsage(false));result.remove("recovery");result.put("status","agent_invalid_checkpoint");result.put("usage_scope","current_execution_only_before_checkpoint_restore");
        var saved=WorkerResults.safeError(Map.of("detail",result),task,snapshot);assertNotNull(saved);
        assertEquals(0,WorkerResults.executionUsage((Map<String,Object>)saved.get("usage"),saved,snapshot).get("model_calls"));
        assertEquals(result.get("usage_scope"),saved.get("usage_scope"));
        result.put("usage",amount(1,10));assertNull(WorkerResults.safeError(Map.of("detail",result),task,snapshot));
    }
    @Test void restoredErrorPreservesBoundMetadataAndCanDeriveItsOwnAttemptUsage() {
        var snapshot=snapshot(amount(1,10));var result=result(snapshot,amount(2,20));result.put("status","agent_checkpoint_unavailable");
        result.put("agent",Map.of("version","workspace-agent-v12","snapshot_id","w:1","trace",List.of(Map.of("event","checkpoint_restored","output",result.get("recovery")))));
        var saved=WorkerResults.safeError(Map.of("detail",result),task,snapshot);assertNotNull(saved);assertNotNull(saved.get("recovery"));assertNotNull(saved.get("agent"));
        assertEquals(1L,WorkerResults.executionUsage((Map<String,Object>)saved.get("usage"),saved,snapshot).get("model_calls"));
    }
    @Test void checkpointNumbersCannotBeSilentlyCoercedFromFractionsOrStrings() throws Exception {
        var json=new com.fasterxml.jackson.databind.ObjectMapper();
        for(String value:List.of("1.5","\"1\"","true")) {
            assertThrows(com.fasterxml.jackson.core.JsonProcessingException.class,()->json.readValue("{\"revision\":"+value+",\"kind\":\"boundary\"}",RecoveryStore.Checkpoint.class));
            assertThrows(com.fasterxml.jackson.core.JsonProcessingException.class,()->json.readValue("{\"expected_checkpoint_revision\":"+value+"}",RecoveryStore.Resume.class));
        }
    }
}
