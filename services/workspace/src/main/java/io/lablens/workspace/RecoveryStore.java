package io.lablens.workspace;

import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** One explicitly approved continuation from a durable, completed-operation boundary. */
@Service
public class RecoveryStore {
    public static class RevisionDeserializer extends com.fasterxml.jackson.databind.JsonDeserializer<Integer> {
        @Override public Integer deserialize(com.fasterxml.jackson.core.JsonParser parser,com.fasterxml.jackson.databind.DeserializationContext context) throws java.io.IOException {
            if (parser.currentToken()!=com.fasterxml.jackson.core.JsonToken.VALUE_NUMBER_INT) return (Integer)context.handleUnexpectedToken(Integer.class,parser);
            return parser.getIntValue();
        }
    }
    public record Checkpoint(@com.fasterxml.jackson.databind.annotation.JsonDeserialize(using=RevisionDeserializer.class) Integer revision,String kind,String operation_id,Map<String,Object> state,String state_sha256) {}
    public record Resume(String approved_snapshot_id,@com.fasterxml.jackson.databind.annotation.JsonDeserialize(using=RevisionDeserializer.class) Integer expected_checkpoint_revision) {}
    static final int MAX_BYTES=512*1024;
    private final JdbcTemplate db;
    private final WorkspaceStore store;
    private final TaskAdmissionStore admission;
    private final TaskEventStore events;
    private final RedisSupport redis;
    private final AuthStore auth;
    private final MeterRegistry metrics;
    private final TransactionTemplate tx;
    @Value("${lablens.checkpoints-enabled:false}") private boolean enabled=false;
    public RecoveryStore(JdbcTemplate db,WorkspaceStore store,TaskAdmissionStore admission,TaskEventStore events,
                         RedisSupport redis,AuthStore auth,MeterRegistry metrics,TransactionTemplate original) {
        this.db=db;this.store=store;this.admission=admission;this.events=events;this.redis=redis;this.auth=auth;this.metrics=metrics;
        tx=new TransactionTemplate(Objects.requireNonNull(original.getTransactionManager()));
        tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }
    private static ApiError conflict(String code,String message) { return new ApiError(HttpStatus.CONFLICT,code,message); }
    private static void uuid(String value) {
        if (value==null || !value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) throw ApiError.bad("Expected a canonical UUID.");
    }
    private Map<String,Object> row(String workspace,String id,boolean lock) {
        var rows=db.queryForList("SELECT *,lease_until>UTC_TIMESTAMP(6) AS lease_active FROM tasks WHERE id=?"+(workspace==null?"":" AND workspace_id=?")+(lock?" FOR UPDATE":""),workspace==null?new Object[]{id}:new Object[]{id,workspace});
        if (rows.isEmpty()) throw ApiError.missing("Task does not exist in this workspace");
        return rows.getFirst();
    }
    private Map<String,Object> latest(String id) {
        var rows=db.queryForList("SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY revision DESC LIMIT 1",id);
        return rows.isEmpty()?null:rows.getFirst();
    }
    public Map<String,Object> checkpoint(String task,String owner,Checkpoint input) {
        if (!enabled) throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"checkpoints_disabled","Durable checkpoints are disabled for this deployment.");
        uuid(task);uuid(owner);
        if (input==null || input.revision()==null || input.revision()<1 || input.revision()>128
            || !Set.of("boundary","operation_started").contains(input.kind()==null?"":input.kind())) throw ApiError.bad("Invalid checkpoint envelope.");
        String payload=null;int bytes=0;
        if (input.kind().equals("boundary")) {
            if (input.operation_id()!=null || input.state()==null || !input.state().keySet().equals(Set.of("payload_json"))
                || !(input.state().get("payload_json") instanceof String text) || input.state_sha256()==null) throw ApiError.bad("Boundary requires a single serialized state payload.");
            payload=text;bytes=text.getBytes(StandardCharsets.UTF_8).length;
            if (bytes>MAX_BYTES) throw new ApiError(HttpStatus.PAYLOAD_TOO_LARGE,"checkpoint_too_large","Checkpoint exceeds the 512 KiB state limit.");
            if (!Requests.sha256(text).equals(input.state_sha256())) throw ApiError.bad("Checkpoint state digest does not match its exact UTF-8 payload.");
            try { store.decode(text); } catch (RuntimeException e) { throw ApiError.bad("Checkpoint state must contain a JSON object."); }
        } else if (input.state()!=null || input.state_sha256()!=null || input.operation_id()==null
            || !input.operation_id().matches("[A-Za-z0-9_.:-]{1,120}")) throw ApiError.bad("Operation receipt requires an operation id and no state.");
        String state=payload;int stateBytes=bytes;
        String envelope=Requests.sha256(store.encode(Arrays.asList(input.revision(),input.kind(),input.operation_id(),input.state_sha256())));
        return tx.execute(status->{
            var taskRow=row(null,task,true);
            if (!"RUNNING".equals(taskRow.get("status")) || !owner.equals(taskRow.get("owner"))
                || !Boolean.TRUE.equals(taskRow.get("lease_active")) && !(taskRow.get("lease_active") instanceof Number n && n.intValue()==1))
                throw conflict("checkpoint_fenced","Execution owner is no longer active; do not start another operation.");
            if (!"agent".equals(store.decode((String)taskRow.get("snapshot_json")).get("mode"))) throw ApiError.bad("Only Agent tasks can write checkpoints.");
            var previous=latest(task);
            int previousRevision=previous==null?0:((Number)previous.get("revision")).intValue();
            if (input.revision()==previousRevision && envelope.equals(previous.get("envelope_sha256"))) return receipt(task,previous,true);
            if (input.revision()!=previousRevision+1) throw conflict("checkpoint_revision_conflict","Checkpoint sequence changed; no automatic operation retry is allowed.");
            // A single decision/tool batch can start several paid operations (for
            // example decision then review). It remains non-resumable until one
            // later boundary durably records the entire completed batch.
            if (input.kind().equals("operation_started") && previous==null)
                throw conflict("checkpoint_pending","An operation requires an initial boundary.");
            db.update("INSERT INTO task_checkpoints(task_id,revision,kind,operation_id,envelope_sha256,state_sha256,state_json,state_bytes) VALUES (?,?,?,?,?,?,?,?)",
                task,input.revision(),input.kind(),input.operation_id(),envelope,input.state_sha256(),state,stateBytes);
            return receipt(task,Objects.requireNonNull(latest(task)),false);
        });
    }
    private Map<String,Object> receipt(String id,Map<String,Object> checkpoint,boolean reused) {
        Map<String,Object> result=new LinkedHashMap<>();result.put("task_id",id);result.put("revision",checkpoint.get("revision"));
        result.put("kind",checkpoint.get("kind"));result.put("state_sha256",checkpoint.get("state_sha256"));result.put("reused",reused);return result;
    }
    private String child(String parent) {
        var children=db.queryForList("SELECT id FROM tasks WHERE parent_task_id=?",String.class,parent);
        return children.isEmpty()?null:children.getFirst();
    }
    private String eligibility(Map<String,Object> task,Map<String,Object> checkpoint,String child) {
        if (child!=null) return "already_resumed";
        if (!enabled) return "unavailable";
        if (Set.of("QUEUED","RUNNING","CANCEL_REQUESTED").contains(task.get("status"))) return "active";
        if (task.get("parent_task_id")!=null || !Set.of("FAILED","CANCELLED").contains(task.get("status"))
            || !"agent".equals(store.decode((String)task.get("snapshot_json")).get("mode"))) return "unavailable";
        if (checkpoint==null) return "no_checkpoint";
        if (!"boundary".equals(checkpoint.get("kind"))) return "unknown_outcome";
        if (task.get("conversation_id")!=null) {
            Long revision=db.queryForObject("SELECT revision FROM conversations WHERE id=?",Long.class,task.get("conversation_id"));
            if (revision==null || revision.longValue()!=((Number)task.get("conversation_seq")).longValue()) return "unavailable";
        }
        return "resumable";
    }
    public Map<String,Object> status(String workspace,String id) {
        var task=row(workspace,id,false);var checkpoint=latest(id);String child=child(id),status=eligibility(task,checkpoint,child);
        Map<String,Object> result=new LinkedHashMap<>();result.put("task_id",id);result.put("status",status);
        result.put("snapshot_id",workspace+":"+task.get("snapshot_revision"));result.put("checkpoint_revision",checkpoint==null?0:checkpoint.get("revision"));
        result.put("parent_task_id",task.get("parent_task_id"));result.put("resumed_task_id",child);
        result.put("enabled",enabled);
        result.put("snapshot_document_count",task.get("snapshot_document_count"));
        var snapshot=store.decode((String)task.get("snapshot_json"));
        result.put("snapshot_documents_sha256",Requests.sha256(MemoryRequests.canonical(snapshot.get("documents"))));
        result.put("reason",switch(status) {
            case "resumable" -> "Completed operations are preserved. Confirm the original snapshot to create one continuation.";
            case "unknown_outcome" -> "An operation was started without a committed completion receipt; replay is blocked.";
            case "active" -> "The task is still active or cancellation has not been acknowledged.";
            case "already_resumed" -> "A durable continuation already exists; open it instead of resubmitting.";
            case "no_checkpoint" -> "No completed checkpoint is available.";
            default -> "This task cannot be resumed: success, continuation depth, mode or newer conversation turn.";
        });
        if (checkpoint!=null) {
            result.put("checkpoint_kind",checkpoint.get("kind"));result.put("state_sha256",checkpoint.get("state_sha256"));result.put("state_bytes",checkpoint.get("state_bytes"));
            if (checkpoint.get("state_json") instanceof String raw) {
                var state=store.decode(raw);var counts=new LinkedHashMap<String,Object>();
                for (String key:List.of("elapsed_ms","next_step")) if (state.get(key) instanceof Number n) counts.put(key,n);
                result.put("consumed",counts);
            }
        }
        return result;
    }
    private WorkspaceStore.Submission previous(String workspace,String key,String hash) {
        var ids=db.queryForList("SELECT id,request_hash FROM tasks WHERE workspace_id=? AND idempotency_key=?",workspace,key);
        if (ids.isEmpty()) return null;
        if (!hash.equals(ids.getFirst().get("request_hash"))) throw conflict("idempotency_conflict","This key belongs to a different request.");
        admission.reused();return new WorkspaceStore.Submission(store.task(workspace,(String)ids.getFirst().get("id")),true);
    }
    public WorkspaceStore.Submission resume(String workspace,String parent,String key,Resume input,AuthStore.Identity who) {
        if (who==null) throw AuthStore.unauthorized();auth.access(workspace,who,true,false);
        Requests.idempotencyKey(key);uuid(parent);
        if (input==null || input.approved_snapshot_id()==null || input.expected_checkpoint_revision()==null || input.expected_checkpoint_revision()<1)
            throw ApiError.bad("Approve the exact original snapshot and checkpoint revision.");
        String requestHash=Requests.sha256(store.encode(Map.of("resume_parent",parent,"approved_snapshot_id",input.approved_snapshot_id(),"expected_checkpoint_revision",input.expected_checkpoint_revision())));
        var previous=previous(workspace,key,requestHash);if (previous!=null) return previous;
        if (!enabled) throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"checkpoints_disabled","Durable checkpoints are disabled for this deployment.");
        if (TransactionSynchronizationManager.isActualTransactionActive()
            && !Objects.equals(TransactionSynchronizationManager.getCurrentTransactionIsolationLevel(),TransactionDefinition.ISOLATION_READ_COMMITTED))
            throw new IllegalStateException("Continuation admission requires READ_COMMITTED when joining an existing transaction");
        var rate=redis.allowSubmission(workspace);
        if (rate!=RedisSupport.AdmissionDecision.ALLOWED) {
            previous=previous(workspace,key,requestHash);if (previous!=null) return previous;
            throw new ApiError(rate==RedisSupport.AdmissionDecision.UNAVAILABLE?HttpStatus.SERVICE_UNAVAILABLE:HttpStatus.TOO_MANY_REQUESTS,
                rate==RedisSupport.AdmissionDecision.UNAVAILABLE?"admission_unavailable":"rate_limited","Continuation admission is unavailable; reconcile this key before retrying.");
        }
        return tx.execute(status->{
            admission.observeTransaction();admission.lock();store.workspace(workspace,true);
            auth.access(workspace,who,true,false);
            var committed=previous(workspace,key,requestHash);if (committed!=null) return committed;
            var task=row(workspace,parent,true);var checkpoint=latest(parent);String existing=child(parent);
            if (!(workspace+":"+task.get("snapshot_revision")).equals(input.approved_snapshot_id())
                || checkpoint==null || ((Number)checkpoint.get("revision")).intValue()!=input.expected_checkpoint_revision())
                throw conflict("stale_recovery_approval","The approved snapshot or checkpoint does not match the durable task.");
            if (existing!=null) return new WorkspaceStore.Submission(store.task(workspace,existing),true);
            if (!"resumable".equals(eligibility(task,checkpoint,null))) throw conflict("recovery_unavailable","This task has no safe resumable boundary; inspect recovery status.");
            int pending=admission.pendingGlobal(admission.globalLimit());
            if (pending>=admission.globalLimit()) throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"global_queue_full","Global task capacity is full.");
            if (admission.pendingWorkspace(workspace)>=admission.workspaceLimit()) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"queue_full","Workspace task capacity is full.");
            String id=UUID.randomUUID().toString();var snapshot=store.decode((String)task.get("snapshot_json"));
            snapshot.put("resume_context",Map.of("parent_task_id",parent,"checkpoint_revision",checkpoint.get("revision"),
                "state",Map.of("payload_json",checkpoint.get("state_json")),"state_sha256",checkpoint.get("state_sha256")));
            var request=store.decode((String)task.get("request_json"));request.put("resume_parent_task_id",parent);
            request.put("approved_snapshot_id",input.approved_snapshot_id());request.put("expected_checkpoint_revision",input.expected_checkpoint_revision());
            db.update("INSERT INTO tasks(id,workspace_id,idempotency_key,request_hash,request_json,snapshot_revision,snapshot_json,snapshot_document_count,status,parent_task_id) VALUES (?,?,?,?,?,?,?,?,'QUEUED',?)",
                id,workspace,key,requestHash,store.encode(request),task.get("snapshot_revision"),store.encode(snapshot),task.get("snapshot_document_count"),parent);
            events.enqueue(id,workspace,((Number)task.get("snapshot_revision")).longValue());
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override public void afterCommit() { admission.accepted(pending+1);metrics.counter("lablens.recovery.children").increment(); }
            });
            return new WorkspaceStore.Submission(store.task(workspace,id),false);
        });
    }
}
