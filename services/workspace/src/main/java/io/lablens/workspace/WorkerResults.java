package io.lablens.workspace;

import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Validates the worker contract and separates current-task cost from cached provenance. */
final class WorkerResults {
    private static final Set<String> SUCCESS=Set.of("excerpts","answered","no_hits","refused","clarification_required");
    private static final Set<String> ERRORS=Set.of("unavailable","provider_error","invalid_citations","agent_timeout","agent_budget_exceeded","agent_protocol_error","agent_tool_error","context_budget_exceeded","agent_cancelled");
    private static final Set<String> COUNTERS=Set.of("model_calls","embedding_requests","embedded_inputs","embedding_cache_hits");
    private static final Set<String> TOKENS=Set.of("prompt_tokens","completion_tokens","total_tokens");
    private WorkerResults() {}

    static boolean canUseProvider(WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        return Set.of("live","agent").contains(task.mode()) || !"bm25".equals(snapshot.get("strategy"));
    }
    static Map<String,Object> validate(Map<String,Object> result,WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        if (result==null || !(result.get("status") instanceof String status) || !SUCCESS.contains(status)) throw new IllegalArgumentException("Worker returned an unsupported success status");
        if ("clarification_required".equals(status) && (!"agent".equals(task.mode()) || !(result.get("clarification") instanceof Map<?,?> clarification) || !(clarification.get("question") instanceof String question) || question.isBlank()))
            throw new IllegalArgumentException("Worker clarification must contain an agent question");
        checkIdentity(result,task,snapshot);
        return usage(result.get("usage"));
    }
    private static void checkIdentity(Map<String,Object> result,WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        if (!task.mode().equals(result.get("mode"))) throw new IllegalArgumentException("Worker result mode does not match task");
        if (!(result.get("retrieval") instanceof Map<?,?> retrieval)
            || !(task.workspace()+":"+task.revision()).equals(retrieval.get("snapshot_id"))
            || snapshot.get("strategy")==null || !snapshot.get("strategy").equals(retrieval.get("strategy"))) {
            throw new IllegalArgumentException("Worker result does not match immutable task snapshot");
        }
        if (snapshot.get("conversation_context") instanceof Map<?,?> expected) {
            if (!(result.get("context") instanceof Map<?,?> context) || !expected.get("id").equals(context.get("conversation_id"))
                || !(context.get("conversation_revision") instanceof Number actual) || actual.longValue()!=((Number)expected.get("revision")).longValue())
                throw new IllegalArgumentException("Worker context does not match immutable conversation snapshot");
        }
    }
    static Map<String,Object> usage(Object raw) { return usage(raw,false); }
    private static Map<String,Object> usage(Object raw,boolean errorResponse) {
        if (!(raw instanceof Map<?,?> input)) throw new IllegalArgumentException("Missing worker usage");
        Map<String,Object> output=new LinkedHashMap<>();
        for (String key:COUNTERS) {
            if (!input.containsKey(key)) throw new IllegalArgumentException("Missing worker usage count");
            boolean nullable=errorResponse && !"model_calls".equals(key) && Boolean.TRUE.equals(input.get("provider_usage_unknown"));
            output.put(key,nonnegativeInteger(input.get(key),nullable));
        }
        output.put("embedding_tokens",nonnegativeInteger(input.get("embedding_tokens"),true));
        if (!(input.get("generation_tokens") instanceof Map<?,?> tokens)) throw new IllegalArgumentException("Missing generation usage");
        Map<String,Object> generation=new LinkedHashMap<>();
        for (String key:TOKENS) {
            if (!tokens.containsKey(key)) throw new IllegalArgumentException("Missing generation token field");
            generation.put(key,nonnegativeInteger(tokens.get(key),true));
        }
        output.put("generation_tokens",generation);
        if (!(input.get("provider_usage_unknown") instanceof Boolean unknown)) throw new IllegalArgumentException("Missing usage certainty");
        boolean missingTokens=output.values().stream().anyMatch(v->v==null) || generation.values().stream().anyMatch(v->v==null);
        output.put("provider_usage_unknown",unknown || missingTokens);
        output.put("budget_exhausted",Boolean.TRUE.equals(input.get("budget_exhausted")));
        output.put("cache_reused",false);
        return output;
    }
    private static Object nonnegativeInteger(Object value,boolean nullable) {
        if (value==null && nullable) return null;
        if (value instanceof Integer i && i>=0 || value instanceof Long l && l>=0 || value instanceof BigInteger b && b.signum()>=0) return value;
        throw new IllegalArgumentException("Invalid worker usage count");
    }
    static Map<String,Object> zeroUsage(boolean cached) {
        Map<String,Object> output=new LinkedHashMap<>();
        for (String key:COUNTERS) output.put(key,0);
        output.put("embedding_tokens",0); output.put("generation_tokens",Map.of("prompt_tokens",0,"completion_tokens",0,"total_tokens",0));
        output.put("provider_usage_unknown",false); output.put("budget_exhausted",false); output.put("cache_reused",cached);
        return output;
    }
    static Map<String,Object> uncertainUsage(boolean externalPossible) {
        Map<String,Object> output=zeroUsage(false);
        if (externalPossible) {
            for (String key:COUNTERS) output.put(key,null);
            output.put("embedding_tokens",null);
            Map<String,Object> tokens=new LinkedHashMap<>(); for (String key:TOKENS) tokens.put(key,null);
            output.put("generation_tokens",tokens); output.put("provider_usage_unknown",true);
        }
        return output;
    }
    static Map<String,Object> safeError(Map<String,Object> response,WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        if (!(response.get("detail") instanceof Map<?,?> detail)) return null;
        Map<String,Object> raw=new LinkedHashMap<>(); detail.forEach((key,value)->{ if (key instanceof String s) raw.put(s,value); });
        if (!(raw.get("status") instanceof String status) || !ERRORS.contains(status)) return null;
        try {
            checkIdentity(raw,task,snapshot);
            Map<String,Object> result=new LinkedHashMap<>();
            result.put("status",raw.get("status")); result.put("mode",task.mode());
            result.put("retrieval",Map.of("snapshot_id",task.workspace()+":"+task.revision(),"strategy",snapshot.get("strategy")));
            result.put("usage",usage(raw.get("usage"),true));
            if (raw.get("context") instanceof Map<?,?> context) result.put("context",safeContext(context));
            if ("agent".equals(task.mode()) && raw.get("agent") instanceof Map<?,?> agent) {
                Map<String,Object> saved=safeAgentTrace(agent,task);
                if (saved!=null) result.put("agent",saved);
            }
            result.put("error",switch ((String)raw.get("status")) {
                case "unavailable" -> "Required worker provider configuration is unavailable.";
                case "invalid_citations" -> "Worker rejected generated citation identifiers.";
                case "agent_timeout" -> "Agent time budget exhausted; no further calls were sent.";
                case "agent_budget_exceeded" -> "Agent step or tool-call budget exhausted before a valid report.";
                case "agent_protocol_error" -> "Agent tool protocol or evidence report was invalid; execution stopped.";
                case "agent_tool_error" -> "Agent local retrieval or source verification failed; execution stopped.";
                case "context_budget_exceeded" -> "Required conversation context exceeds the configured budget; no further provider call was sent.";
                case "agent_cancelled" -> "Cancellation observed; no subsequent agent work was started.";
                default -> "Provider request failed; no automatic retry was sent.";
            });
            return result;
        } catch (IllegalArgumentException e) { return null; }
    }
    private static Map<String,Object> safeAgentTrace(Map<?,?> input,WorkspaceStore.Claimed task) {
        if (!(task.workspace()+":"+task.revision()).equals(input.get("snapshot_id"))
                || !Set.of("workspace-agent-v1","workspace-agent-v2","workspace-agent-v3").contains(input.get("version"))
                || !(input.get("trace") instanceof java.util.List<?> trace) || trace.size()>64) return null;
        Map<String,Object> saved=new LinkedHashMap<>();
        for (String key:Set.of("version","implementation_sha256","state","snapshot_id","documents_sha256","max_steps","max_model_calls","max_tool_calls","steps","tool_calls","budget_seconds","verified_chunk_ids","decision_protocol","review_reserve_seconds")) {
            if (input.containsKey(key)) saved.put(key,input.get(key));
        }
        java.util.List<Map<String,Object>> clean=new java.util.ArrayList<>();
        for (Object item:trace) {
            if (!(item instanceof Map<?,?> event) || !(event.get("event") instanceof String kind)) return null;
            if (!Set.of("model_decision","model_decision_rejected","tool_started","tool","tool_rejected","source_validation","review_started","review_completed","stopped").contains(kind)) return null;
            Map<String,Object> selected=new LinkedHashMap<>();
            for (String key:Set.of("event","step","name","tools","arguments","output","usage","state","status","evidence_verdict","scope","source_validation")) {
                if (event.containsKey(key)) selected.put(key,event.get(key));
            }
            clean.add(selected);
        }
        saved.put("trace",clean); return saved;
    }
    private static Map<String,Object> safeContext(Map<?,?> input) {
        Map<String,Object> saved=new LinkedHashMap<>();
        for (String key:Set.of("version","implementation_sha256","conversation_id","conversation_revision","pinned_state","state_authority","history_authority","persistence","automatic_fact_memory","input_turns","omitted_turns")) {
            if (input.containsKey(key)) saved.put(key,input.get(key));
        }
        if (input.get("context_budget") instanceof Map<?,?> budget) {
            Map<String,Object> selected=new LinkedHashMap<>();
            for (String key:Set.of("estimator","exact_tokenizer","limitation","window_units","output_reserve","safety_margin")) if (budget.containsKey(key)) selected.put(key,budget.get(key));
            if (budget.get("calls") instanceof java.util.List<?> calls && calls.size()<=64) selected.put("calls",calls);
            saved.put("context_budget",selected);
        }
        return saved;
    }
    static Map<String,Object> cachedResult(Map<String,Object> original,Map<String,Object> originUsage) {
        Map<String,Object> result=new LinkedHashMap<>(original);
        result.put("origin_usage",originUsage);
        result.put("usage",zeroUsage(true));
        result.put("usage_scope","Current task uses zero provider calls; origin_usage belongs to the historical cached computation.");
        return result;
    }
}
