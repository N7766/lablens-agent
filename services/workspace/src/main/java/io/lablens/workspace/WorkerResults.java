package io.lablens.workspace;

import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Validates the worker contract and separates current-task cost from cached provenance. */
final class WorkerResults {
    private static final Set<String> SUCCESS=Set.of("excerpts","answered","no_hits","refused","clarification_required");
    private static final Set<String> ERRORS=Set.of("unavailable","provider_error","invalid_citations","agent_timeout","agent_budget_exceeded","agent_protocol_error","agent_tool_error","context_budget_exceeded","agent_cancelled","invalid_memory_context","agent_checkpoint_unavailable","agent_invalid_checkpoint");
    private static final Set<String> COUNTERS=Set.of("model_calls","embedding_requests","embedded_inputs","embedding_cache_hits");
    private static final Set<String> TOKENS=Set.of("prompt_tokens","completion_tokens","total_tokens");
    private static final Set<String> TRANSPORT_EXCEPTIONS=Set.of(
        "HTTPDeadlineExceeded","TimeoutException","ConnectTimeout","ReadTimeout","WriteTimeout","PoolTimeout","HTTPStatusError",
        "ConnectError","ReadError","WriteError","CloseError","ProxyError","LocalProtocolError","RemoteProtocolError",
        "ProtocolError","UnsupportedProtocol","DecodingError","TooManyRedirects","RequestError","TransportError","HTTPError",
        "JSONDecodeError","ValueError","KeyError","IndexError","TypeError","ExecutionCancelled","CancelledError","KeyboardInterrupt","unknown");
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
        if (snapshot.get("memory_context") instanceof Map<?,?> expectedMemory) checkedMemory(result.get("memory"),expectedMemory);
        if (snapshot.get("conversation_context") instanceof Map<?,?> expected) {
            if (!(result.get("context") instanceof Map<?,?> context) || !expected.get("id").equals(context.get("conversation_id"))
                || !(context.get("conversation_revision") instanceof Number actual) || actual.longValue()!=((Number)expected.get("revision")).longValue())
                throw new IllegalArgumentException("Worker context does not match immutable conversation snapshot");
        }
        if (snapshot.get("resume_context") instanceof Map<?,?> expected) {
            if (result.containsKey("recovery")) {
                checkedRecovery(result.get("recovery"),expected);
                if (!"cumulative_original_and_resumed_execution".equals(result.get("usage_scope"))) throw new IllegalArgumentException("Restored usage must be explicitly cumulative.");
            } else {
                if (SUCCESS.contains(result.get("status")) || !"current_execution_only_before_checkpoint_restore".equals(result.get("usage_scope")))
                    throw new IllegalArgumentException("A continuation must identify restoration or a zero-call preflight failure.");
                var local=usage(result.get("usage"),true);
                if (Boolean.TRUE.equals(local.get("provider_usage_unknown")) || COUNTERS.stream().anyMatch(k->!sameInteger(local.get(k),0))
                    || !sameInteger(local.get("embedding_tokens"),0) || ((Map<?,?>)local.get("generation_tokens")).values().stream().anyMatch(v->!sameInteger(v,0)))
                    throw new IllegalArgumentException("Preflight failure cannot claim inherited provider usage.");
            }
        }
    }
    private static Map<String,Object> checkpointState(Map<?,?> expected) {
        if (!(expected.get("state") instanceof Map<?,?> wrapper) || !(wrapper.get("payload_json") instanceof String raw)
            || !Requests.sha256(raw).equals(expected.get("state_sha256"))) throw new IllegalArgumentException("Invalid immutable checkpoint.");
        try { return new com.fasterxml.jackson.databind.ObjectMapper().readValue(raw,new com.fasterxml.jackson.core.type.TypeReference<Map<String,Object>>() {}); }
        catch (com.fasterxml.jackson.core.JsonProcessingException e) { throw new IllegalArgumentException("Invalid immutable checkpoint.",e); }
    }
    private static Map<String,Object> checkedRecovery(Object raw,Map<?,?> expected) {
        if (!(raw instanceof Map<?,?> actual) || !java.util.Objects.equals(expected.get("parent_task_id"),actual.get("parent_task_id"))
            || !java.util.Objects.equals(expected.get("state_sha256"),actual.get("state_sha256"))
            || !sameInteger(actual.get("checkpoint_revision"),((Number)expected.get("checkpoint_revision")).longValue())
            || !Boolean.FALSE.equals(actual.get("downtime_charged"))) throw new IllegalArgumentException("Worker recovery provenance mismatch.");
        var state=checkpointState(expected);
        if (!(state.get("result") instanceof Map<?,?> previous) || !(previous.get("usage") instanceof Map<?,?> previousUsage)
            || !(previous.get("agent") instanceof Map<?,?> previousAgent)) throw new IllegalArgumentException("Invalid checkpoint counters.");
        var expectedCounters=new LinkedHashMap<String,Object>();expectedCounters.put("restored_next_step",state.get("next_step"));expectedCounters.put("restored_elapsed_ms",state.get("elapsed_ms"));
        expectedCounters.put("restored_model_calls",previousUsage.get("model_calls"));expectedCounters.put("restored_tool_calls",previousAgent.get("tool_calls"));
        for (var pair:expectedCounters.entrySet())
            if (!(pair.getValue() instanceof Number n) || !sameInteger(actual.get(pair.getKey()),n.longValue())) throw new IllegalArgumentException("Restored checkpoint counter mismatch.");
        Map<String,Object> saved=new LinkedHashMap<>();
        for (String key:Set.of("parent_task_id","checkpoint_revision","state_sha256","restored_next_step","restored_elapsed_ms","restored_model_calls","restored_tool_calls","downtime_charged")) saved.put(key,actual.get(key));
        saved.put("usage_scope","result.usage is cumulative across the original task and continuation; execution_usage is this task's delta, with unknown values retained.");
        return saved;
    }
    /** Only derive per-attempt usage when the worker proves it restored the frozen state. */
    static Map<String,Object> executionUsage(Map<String,Object> cumulative,Map<String,Object> result,Map<String,Object> snapshot) {
        if (!(snapshot.get("resume_context") instanceof Map<?,?> expected) || !result.containsKey("recovery")) return cumulative;
        checkedRecovery(result.get("recovery"),expected);
        var state=checkpointState(expected);var previous=(Map<?,?>)state.get("result");
        Map<String,Object> inherited=usage(previous.get("usage"));Map<String,Object> delta=new LinkedHashMap<>(cumulative);
        for (String key:COUNTERS) delta.put(key,difference(cumulative.get(key),inherited.get(key)));
        delta.put("embedding_tokens",difference(cumulative.get("embedding_tokens"),inherited.get("embedding_tokens")));
        Map<?,?> currentTokens=(Map<?,?>)cumulative.get("generation_tokens"),priorTokens=(Map<?,?>)inherited.get("generation_tokens");
        Map<String,Object> tokens=new LinkedHashMap<>();for (String key:TOKENS) tokens.put(key,difference(currentTokens.get(key),priorTokens.get(key)));delta.put("generation_tokens",tokens);
        delta.put("provider_usage_unknown",Boolean.TRUE.equals(cumulative.get("provider_usage_unknown")) || Boolean.TRUE.equals(inherited.get("provider_usage_unknown")) || tokens.containsValue(null) || delta.containsValue(null));
        return delta;
    }
    private static Object difference(Object current,Object inherited) {
        if (current==null || inherited==null) return null;
        long value=((Number)current).longValue()-((Number)inherited).longValue();
        if (value<0) throw new IllegalArgumentException("Cumulative usage cannot decrease after continuation.");return value;
    }
    static Map<String,Object> continuedResult(Map<String,Object> result,Map<String,Object> snapshot) {
        Map<String,Object> saved=new LinkedHashMap<>(result);
        if (snapshot.get("resume_context") instanceof Map<?,?> expected && result.containsKey("recovery")) saved.put("recovery",checkedRecovery(result.get("recovery"),expected));
        return saved;
    }
    private static boolean sameInteger(Object value,long expected) {
        try { return ((Number)nonnegativeInteger(value,false)).longValue()==expected; }
        catch (IllegalArgumentException e) { return false; }
    }
    /** Memory metadata is provenance for explicitly selected hints, never evidence citations. */
    private static Map<String,Object> checkedMemory(Object raw,Map<?,?> expected) {
        if (!(raw instanceof Map<?,?> memory)
            || !"workspace-memory-context-v1".equals(memory.get("version"))
            || !expected.get("workspace_id").equals(memory.get("workspace_id"))
            || !sameInteger(memory.get("revision"),((Number)expected.get("revision")).longValue())
            || !Boolean.FALSE.equals(memory.get("automatic_memory"))
            || !"explicit_shared_member_hints_not_verified_evidence".equals(memory.get("authority"))
            || !(memory.get("implementation_sha256") instanceof String implementation) || !implementation.matches("[a-f0-9]{64}")
            || !(expected.get("entries") instanceof java.util.List<?> expectedEntries)
            || !(memory.get("entries") instanceof java.util.List<?> actualEntries) || actualEntries.size()!=expectedEntries.size()
            || !sameInteger(memory.get("selected_count"),expectedEntries.size()))
            throw new IllegalArgumentException("Worker memory identity does not match selected task snapshot");
        String canonical=MemoryRequests.canonical(expected);
        if (!Requests.sha256(canonical).equals(memory.get("context_sha256"))
            || !sameInteger(memory.get("serialized_utf8_bytes"),canonical.getBytes(java.nio.charset.StandardCharsets.UTF_8).length))
            throw new IllegalArgumentException("Worker memory hash does not match selected task snapshot");
        var entries=new java.util.ArrayList<Map<String,Object>>();
        for (int i=0;i<expectedEntries.size();i++) {
            if (!(expectedEntries.get(i) instanceof Map<?,?> e) || !(actualEntries.get(i) instanceof Map<?,?> a)
                || !(e.get("source") instanceof Map<?,?> es) || !(a.get("source") instanceof Map<?,?> as)) throw new IllegalArgumentException("Invalid memory provenance");
            Map<String,Object> entry=new LinkedHashMap<>();
            for (String key:java.util.List.of("id","key","created_by","scope","expires_at")) {
                if (!java.util.Objects.equals(e.get(key),a.get(key))) throw new IllegalArgumentException("Worker memory entry does not match selected task snapshot");
                entry.put(key,a.get(key));
            }
            if (!sameInteger(a.get("version"),((Number)e.get("version")).longValue())) throw new IllegalArgumentException("Worker memory version mismatch");
            entry.put("version",((Number)a.get("version")).longValue());
            Map<String,Object> source=new LinkedHashMap<>();
            for (String key:java.util.List.of("document_id","document_sha256","snapshot_id")) {
                if (!java.util.Objects.equals(es.get(key),as.get(key))) throw new IllegalArgumentException("Worker memory source mismatch");
                source.put(key,as.get(key));
            }
            if (!sameInteger(as.get("document_version"),((Number)es.get("document_version")).longValue())
                || !Requests.sha256((String)es.get("quote")).equals(as.get("quote_sha256"))) throw new IllegalArgumentException("Worker memory quote/version mismatch");
            source.put("document_version",((Number)as.get("document_version")).longValue());source.put("quote_sha256",as.get("quote_sha256"));entry.put("source",source);entries.add(entry);
        }
        Map<String,Object> safe=new LinkedHashMap<>();
        for (String key:java.util.List.of("version","implementation_sha256","workspace_id","revision","context_sha256","serialized_utf8_bytes","selected_count","authority","automatic_memory")) safe.put(key,memory.get(key));
        safe.put("admission_state","immutable_selection_checked_by_workspace_service");safe.put("expiry_policy","rechecked_at_worker_execution_start");
        safe.put("document_version_validation","workspace_service; worker_checks_current_text_hash_and_quote");safe.put("entries",entries);return safe;
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
            if (snapshot.containsKey("resume_context")) result.put("usage_scope",raw.get("usage_scope"));
            if (snapshot.get("resume_context") instanceof Map<?,?> expected && raw.containsKey("recovery")) result.put("recovery",checkedRecovery(raw.get("recovery"),expected));
            if (snapshot.get("memory_context") instanceof Map<?,?> expectedMemory) result.put("memory",checkedMemory(raw.get("memory"),expectedMemory));
            if (raw.get("context") instanceof Map<?,?> context) result.put("context",safeContext(context));
            if ("agent".equals(task.mode()) && raw.get("agent") instanceof Map<?,?> agent) {
                Map<String,Object> saved=safeAgentTrace(agent,task);
                if (saved!=null) result.put("agent",saved);
            }
            result.put("error",switch ((String)raw.get("status")) {
                case "unavailable" -> "Required worker provider configuration is unavailable.";
                case "invalid_citations" -> "Worker rejected generated citation identifiers.";
                case "agent_timeout" -> "An Agent request or execution time limit was reached; no further calls were sent.";
                case "agent_budget_exceeded" -> "Agent step or tool-call budget exhausted before a valid report.";
                case "agent_protocol_error" -> "Agent tool protocol or evidence report was invalid; execution stopped.";
                case "agent_tool_error" -> "Agent local retrieval or source verification failed; execution stopped.";
                case "context_budget_exceeded" -> "Required conversation context exceeds the configured budget; no further provider call was sent.";
                case "invalid_memory_context" -> "Selected memory is no longer usable at worker execution; no provider call was sent.";
                case "agent_cancelled" -> "Cancellation observed; no subsequent agent work was started.";
                case "agent_checkpoint_unavailable" -> "Durable checkpoint confirmation failed; no subsequent operation was started.";
                case "agent_invalid_checkpoint" -> "Checkpoint identity, state or cumulative budget was invalid; continuation stopped.";
                default -> "Provider request failed; no automatic retry was sent.";
            });
            return result;
        } catch (IllegalArgumentException e) { return null; }
    }
    private static Map<String,Object> safeAgentTrace(Map<?,?> input,WorkspaceStore.Claimed task) {
        if (!(task.workspace()+":"+task.revision()).equals(input.get("snapshot_id"))
                || !Set.of("workspace-agent-v1","workspace-agent-v2","workspace-agent-v3","workspace-agent-v4","workspace-agent-v5","workspace-agent-v6","workspace-agent-v7","workspace-agent-v8","workspace-agent-v9","workspace-agent-v10","workspace-agent-v11","workspace-agent-v12").contains(input.get("version"))
                || !(input.get("trace") instanceof java.util.List<?> trace) || trace.size()>128) return null;
        Map<String,Object> saved=new LinkedHashMap<>();
        for (String key:Set.of("version","implementation_sha256","state","snapshot_id","documents_sha256","max_steps","max_model_calls","max_tool_calls","steps","tool_calls","budget_seconds","verified_chunk_ids","decision_protocol","review_reserve_seconds")) {
            if (input.containsKey(key)) saved.put(key,input.get(key));
        }
        if (input.get("telemetry_version") instanceof String version && Set.of("runtime-stages-v1","runtime-stages-v2").contains(version)) saved.put("telemetry_version",version);
        java.util.List<Map<String,Object>> clean=new java.util.ArrayList<>();
        for (Object item:trace) {
            if (!(item instanceof Map<?,?> event) || !(event.get("event") instanceof String kind)) return null;
            if (Set.of("stage_started","stage_finished","request_started","request_finished").contains(kind)) {
                Map<String,Object> timing=safeTiming(event,kind);
                if (timing==null) return null;
                clean.add(timing); continue;
            }
            if (!Set.of("model_decision","model_decision_rejected","tool_started","tool","tool_rejected","source_validation","answer_contract","review_coverage_slots","evidence_quality","review_started","review_completed","stopped","memory_context","checkpoint_restored").contains(kind)) return null;
            Map<String,Object> selected=new LinkedHashMap<>();
            for (String key:Set.of("event","step","name","tools","arguments","output","usage","state","status","evidence_verdict","scope","source_validation","clarification_policy","memory")) {
                if (event.containsKey(key)) selected.put(key,event.get(key));
            }
            clean.add(selected);
        }
        saved.put("trace",clean); return saved;
    }
    private static Map<String,Object> safeTiming(Map<?,?> event,String kind) {
        if (!(event.get("phase") instanceof String phase) || !Set.of("decision","tool","review").contains(phase)) return null;
        Map<String,Object> result=new LinkedHashMap<>(); result.put("event",kind); result.put("phase",phase);
        for (String key:Set.of("stage_id","parent_stage_id","request_id")) if (event.containsKey(key)) {
            Object value=event.get(key);
            if (value==null && key.equals("parent_stage_id")) result.put(key,null);
            else if (value instanceof String id && id.matches(key.equals("request_id")?"r[1-9][0-9]{0,3}":"s[1-9][0-9]{0,3}")) result.put(key,id);
            else return null;
        }
        if (!result.containsKey("stage_id") || kind.startsWith("request_")&&!result.containsKey("request_id")) return null;
        for (String key:Set.of("step","request_body_bytes","http_status")) if (event.containsKey(key)) {
            Object value=event.get(key);
            if (value==null && key.equals("http_status")) result.put(key,null);
            else if (value instanceof Integer i && i>=0 || value instanceof Long l && l>=0) result.put(key,value);
            else return null;
        }
        for (String key:Set.of("elapsed_ms","remaining_ms","duration_ms","timeout_ms")) if (event.containsKey(key)) {
            if (!(event.get(key) instanceof Number value) || !Double.isFinite(value.doubleValue()) || value.doubleValue()<0) return null;
            result.put(key,value);
        }
        if (!result.containsKey("elapsed_ms") || !result.containsKey("remaining_ms")) return null;
        if (kind.endsWith("_finished") && (!result.containsKey("duration_ms") || !(event.get("outcome") instanceof String outcome)
                || !Set.of("returned","timeout","cancelled","http_error","transport_error","error","rejected").contains(outcome))) return null;
        if (kind.endsWith("_finished")) result.put("outcome",event.get("outcome"));
        if ("request_finished".equals(kind)) {
            if ("timeout".equals(event.get("outcome")) && event.get("timeout_phase") instanceof String timeout
                    && Set.of("connect","read","write","pool","deadline","unknown").contains(timeout)) result.put("timeout_phase",timeout);
            if (!"returned".equals(event.get("outcome")) && event.get("exception_type") instanceof String exception
                    && TRANSPORT_EXCEPTIONS.contains(exception)) result.put("exception_type",exception);
            if ("complete_httpx_response".equals(event.get("response_observation"))) result.put("response_observation","complete_httpx_response");
        }
        if (event.get("name") instanceof String name && Set.of("search_workspace","read_source_passages","verify_sources","request_clarification","submit_report","verify_and_submit_report").contains(name)) result.put("name",name);
        if ("provider_http".equals(event.get("transport"))) result.put("transport","provider_http");
        for (String key:Set.of("response_received","usage_known")) if (event.get(key) instanceof Boolean value) result.put(key,value);
        if (event.get("provider_acceptance") instanceof String value && Set.of("unknown","response_received").contains(value)) result.put("provider_acceptance",value);
        if (event.get("usage") instanceof Map<?,?> input) {
            Map<String,Object> usage=new LinkedHashMap<>();
            for (String key:TOKENS) {
                Object value=input.get(key);
                if (value==null || value instanceof Integer i && i>=0 || value instanceof Long l && l>=0) usage.put(key,value);
                else return null;
            }
            result.put("usage",usage);
            if (usage.values().stream().anyMatch(v->v==null)) result.put("usage_known",false);
        }
        return result;
    }
    private static Map<String,Object> safeContext(Map<?,?> input) {
        Map<String,Object> saved=new LinkedHashMap<>();
        for (String key:Set.of("version","implementation_sha256","conversation_id","conversation_revision","pinned_state","state_authority","history_authority","persistence","automatic_fact_memory","input_turns","omitted_turns","summary","summary_policy","unavailable_legacy_user_turns")) {
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
