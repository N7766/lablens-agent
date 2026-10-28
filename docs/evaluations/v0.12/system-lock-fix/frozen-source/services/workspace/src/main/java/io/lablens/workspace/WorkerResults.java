package io.lablens.workspace;

import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Validates the worker contract and separates current-task cost from cached provenance. */
final class WorkerResults {
    private static final Set<String> SUCCESS=Set.of("excerpts","answered","no_hits","refused");
    private static final Set<String> ERRORS=Set.of("unavailable","provider_error","invalid_citations");
    private static final Set<String> COUNTERS=Set.of("model_calls","embedding_requests","embedded_inputs","embedding_cache_hits");
    private static final Set<String> TOKENS=Set.of("prompt_tokens","completion_tokens","total_tokens");
    private WorkerResults() {}

    static boolean canUseProvider(WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        return "live".equals(task.mode()) || !"bm25".equals(snapshot.get("strategy"));
    }
    static Map<String,Object> validate(Map<String,Object> result,WorkspaceStore.Claimed task,Map<String,Object> snapshot) {
        if (result==null || !(result.get("status") instanceof String status) || !SUCCESS.contains(status)) throw new IllegalArgumentException("Worker returned an unsupported success status");
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
    }
    static Map<String,Object> usage(Object raw) {
        if (!(raw instanceof Map<?,?> input)) throw new IllegalArgumentException("Missing worker usage");
        Map<String,Object> output=new LinkedHashMap<>();
        for (String key:COUNTERS) output.put(key,nonnegativeInteger(input.get(key),false));
        output.put("embedding_tokens",nonnegativeInteger(input.get("embedding_tokens"),true));
        if (!(input.get("generation_tokens") instanceof Map<?,?> tokens)) throw new IllegalArgumentException("Missing generation usage");
        Map<String,Object> generation=new LinkedHashMap<>();
        for (String key:TOKENS) {
            if (!tokens.containsKey(key)) throw new IllegalArgumentException("Missing generation token field");
            generation.put(key,nonnegativeInteger(tokens.get(key),true));
        }
        output.put("generation_tokens",generation);
        if (!(input.get("provider_usage_unknown") instanceof Boolean unknown)) throw new IllegalArgumentException("Missing usage certainty");
        boolean missingTokens=output.get("embedding_tokens")==null || generation.values().stream().anyMatch(v->v==null);
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
            result.put("usage",usage(raw.get("usage")));
            result.put("error",switch ((String)raw.get("status")) {
                case "unavailable" -> "Required worker provider configuration is unavailable.";
                case "invalid_citations" -> "Worker rejected generated citation identifiers.";
                default -> "Provider request failed; no automatic retry was sent.";
            });
            return result;
        } catch (IllegalArgumentException e) { return null; }
    }
    static Map<String,Object> cachedResult(Map<String,Object> original,Map<String,Object> originUsage) {
        Map<String,Object> result=new LinkedHashMap<>(original);
        result.put("origin_usage",originUsage);
        result.put("usage",zeroUsage(true));
        result.put("usage_scope","Current task uses zero provider calls; origin_usage belongs to the historical cached computation.");
        return result;
    }
}
