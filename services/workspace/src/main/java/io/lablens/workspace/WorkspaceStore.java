package io.lablens.workspace;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.TransactionDefinition;

@Service
public class WorkspaceStore {
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final TransactionTemplate claimTx;
    private final ObjectMapper json;
    private final RedisSupport redis;
    private final MeterRegistry metrics;
    private final int maxPending;
    static final int MAX_DOCUMENTS=100;
    static final int MAX_CHARACTERS=200000;

    public WorkspaceStore(JdbcTemplate db, TransactionTemplate tx, ObjectMapper json, RedisSupport redis, MeterRegistry metrics,
                          @Value("${lablens.max-pending-tasks}") int maxPending) {
        this.db=db; this.tx=tx; this.json=json; this.redis=redis; this.metrics=metrics; this.maxPending=maxPending;
        this.claimTx=new TransactionTemplate(Objects.requireNonNull(tx.getTransactionManager()));
        this.claimTx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }
    String encode(Object value) {
        try { return json.writeValueAsString(value); } catch (JsonProcessingException e) { throw new IllegalStateException(e); }
    }
    Map<String,Object> decode(String value) {
        try { return json.readValue(value,new TypeReference<>() {}); } catch (JsonProcessingException e) { throw new IllegalStateException("Invalid persisted JSON",e); }
    }
    public List<Map<String,Object>> workspaces() {
        return db.query("SELECT id,name,revision,created_at FROM workspaces ORDER BY created_at,id LIMIT 1000",this::workspaceRow);
    }
    private Map<String,Object> workspaceRow(ResultSet rs,int row) throws SQLException {
        return Map.of("id",rs.getString("id"),"name",rs.getString("name"),"revision",rs.getLong("revision"),"created_at",instant(rs,"created_at"));
    }
    public Map<String,Object> createWorkspace(Requests.Workspace input) {
        String id=UUID.randomUUID().toString();
        db.update("INSERT INTO workspaces(id,name) VALUES (?,?)",id,input.name().strip());
        return workspace(id,false);
    }
    Map<String,Object> workspace(String id,boolean lock) {
        List<Map<String,Object>> rows=db.query("SELECT id,name,revision,created_at FROM workspaces WHERE id=?"+(lock?" FOR UPDATE":""),this::workspaceRow,id);
        if (rows.isEmpty()) throw ApiError.missing("Workspace does not exist");
        return rows.getFirst();
    }
    private List<Map<String,Object>> documentRows(String workspace) {
        return db.query("SELECT id,title,body,source,version,updated_at FROM documents WHERE workspace_id=? ORDER BY id",(rs,n)-> {
            Map<String,Object> d=new LinkedHashMap<>();
            d.put("id",rs.getString("id")); d.put("title",rs.getString("title")); d.put("text",rs.getString("body")); d.put("source",rs.getString("source"));
            d.put("version",rs.getLong("version")); d.put("updated_at",instant(rs,"updated_at")); return d;
        },workspace);
    }
    public Map<String,Object> documents(String workspace) {
        return tx.execute(status -> {
            Map<String,Object> w=workspace(workspace,true);
            return Map.of("workspace_id",workspace,"revision",w.get("revision"),"documents",documentRows(workspace));
        });
    }
    public Map<String,Object> document(String workspace,String id) {
        Requests.documentId(id);
        @SuppressWarnings("unchecked") List<Map<String,Object>> rows=(List<Map<String,Object>>) documents(workspace).get("documents");
        return rows.stream().filter(row->id.equals(row.get("id"))).findFirst().orElseThrow(()->ApiError.missing("Document does not exist"));
    }
    public Map<String,Object> putDocument(String workspace,String id,Requests.Document input) {
        Requests.documentId(id);
        return tx.execute(status -> {
            long revision=((Number)workspace(workspace,true).get("revision")).longValue()+1;
            List<Map<String,Object>> documents=documentRows(workspace);
            boolean exists=documents.stream().anyMatch(row->id.equals(row.get("id")));
            long chars=documents.stream().filter(row->!id.equals(row.get("id"))).mapToLong(row->((String)row.get("text")).length()).sum()+input.text().length();
            if ((!exists && documents.size()>=MAX_DOCUMENTS) || chars>MAX_CHARACTERS) throw new ApiError(HttpStatus.PAYLOAD_TOO_LARGE,"workspace_capacity","Workspace allows at most 100 documents and 200000 text characters");
            String source=sourceLabel(input.source(),id);
            db.update("INSERT INTO documents(workspace_id,id,title,body,source,version) VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE title=?,body=?,source=?,version=?,updated_at=UTC_TIMESTAMP(6)",workspace,id,input.title(),input.text(),source,revision,input.title(),input.text(),source,revision);
            db.update("INSERT INTO document_versions(workspace_id,document_id,revision,title,body,source) VALUES (?,?,?,?,?,?)",workspace,id,revision,input.title(),input.text(),source);
            db.update("UPDATE workspaces SET revision=? WHERE id=?",revision,workspace);
            metrics.counter("lablens.documents.mutations").increment();
            return Map.of("id",id,"workspace_id",workspace,"revision",revision,"version",revision,"title",input.title(),"text",input.text(),"source",source);
        });
    }
    public void deleteDocument(String workspace,String id) {
        Requests.documentId(id);
        tx.executeWithoutResult(status -> {
            long revision=((Number)workspace(workspace,true).get("revision")).longValue()+1;
            List<Map<String,Object>> rows=documentRows(workspace);
            Map<String,Object> old=rows.stream().filter(row->id.equals(row.get("id"))).findFirst().orElseThrow(()->ApiError.missing("Document does not exist"));
            db.update("INSERT INTO document_versions(workspace_id,document_id,revision,title,body,source,deleted) VALUES (?,?,?,?,?,?,TRUE)",workspace,id,revision,old.get("title"),old.get("text"),old.get("source"));
            db.update("DELETE FROM documents WHERE workspace_id=? AND id=?",workspace,id);
            db.update("UPDATE workspaces SET revision=? WHERE id=?",revision,workspace);
            metrics.counter("lablens.documents.mutations").increment();
        });
    }
    public record Submission(Map<String,Object> task,boolean reused) {}
    private Map<String,Object> conversationRow(ResultSet rs,int row) throws SQLException {
        Map<String,Object> c=new LinkedHashMap<>();
        for (String field:List.of("id","workspace_id","title")) c.put(field,rs.getString(field));
        c.put("revision",rs.getLong("revision")); c.put("state",decode(rs.getString("state_json")));
        for (String field:List.of("created_at","updated_at")) c.put(field,instant(rs,field));
        return c;
    }
    private Map<String,Object> conversation(String workspace,String id,boolean lock) {
        List<Map<String,Object>> rows=db.query("SELECT * FROM conversations WHERE workspace_id=? AND id=?"+(lock?" FOR UPDATE":""),this::conversationRow,workspace,id);
        if (rows.isEmpty()) throw ApiError.missing("Conversation does not exist in this workspace");
        return rows.getFirst();
    }
    public Map<String,Object> createConversation(String workspace,Requests.Conversation input) {
        Requests.Conversation request=input.normalized();
        return tx.execute(status->{
            workspace(workspace,true);
            String id=UUID.randomUUID().toString();
            db.update("INSERT INTO conversations(id,workspace_id,title,state_json) VALUES (?,?,?,?)",id,workspace,request.title(),encode(request.state().initial()));
            Map<String,Object> result=conversation(workspace,id,false); result.put("turns",List.of()); result.put("omitted_turns",0); return result;
        });
    }
    public List<Map<String,Object>> conversations(String workspace) {
        workspace(workspace,false);
        return db.query("SELECT * FROM conversations WHERE workspace_id=? ORDER BY updated_at DESC,id DESC LIMIT 100",this::conversationRow,workspace);
    }
    public Map<String,Object> conversation(String workspace,String id) {
        return tx.execute(status->{
            // A consistent read sees task completion directly; no asynchronous conversation summary can become stale.
            Map<String,Object> result=conversation(workspace,id,false);
            List<Map<String,Object>> turns=conversationTurns(workspace,id,100,false);
            result.put("turns",turns);
            result.put("omitted_turns",Math.max(0,((Number)result.get("revision")).longValue()-turns.size()));
            return result;
        });
    }
    private List<Map<String,Object>> conversationTurns(String workspace,String id,int limit,boolean terminalOnly) {
        List<Map<String,Object>> turns=db.query("SELECT id,conversation_seq,status,snapshot_revision,error_code,created_at,finished_at,"
            +"JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.question')) AS question,"
            +"LEFT(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.answer')),4000) AS answer,"
            +"CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.answer'))) AS answer_length,"
            +"JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.status')) AS result_status,"
            +"JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.agent.state')) AS agent_state "
            +"FROM tasks WHERE workspace_id=? AND conversation_id=?"+(terminalOnly?" AND status IN ('SUCCEEDED','FAILED','CANCELLED')":"")
            +" ORDER BY conversation_seq DESC LIMIT ?",(rs,n)->{
                Map<String,Object> turn=new LinkedHashMap<>();
                turn.put("seq",rs.getLong("conversation_seq")); turn.put("task_id",rs.getString("id"));
                for (String field:List.of("question","status","answer","result_status","agent_state","error_code")) turn.put(field,rs.getString(field));
                turn.put("answer_truncated",rs.getLong("answer_length")>4000);
                turn.put("snapshot_id",workspace+":"+rs.getLong("snapshot_revision"));
                for (String field:List.of("created_at","finished_at")) turn.put(field,instant(rs,field));
                return turn;
            },workspace,id,limit);
        Collections.reverse(turns); return turns;
    }
    public Submission submit(String workspace,String key,Requests.Task input) {
        Requests.idempotencyKey(key);
        Requests.Task request=input.normalized();
        String requestJson=encode(request.fields());
        return submit(workspace,key,request,requestJson,null,null);
    }
    public Submission submitTurn(String workspace,String conversation,String key,Requests.Turn input) {
        Requests.idempotencyKey(key);
        Requests.Turn request=input.normalized();
        return submit(workspace,key,request.task(),encode(request.fields(conversation)),conversation,request);
    }
    private Submission submit(String workspace,String key,Requests.Task request,String requestJson,String conversationId,Requests.Turn turn) {
        String hash=Requests.sha256(requestJson);
        return tx.execute(status -> {
            long revision=((Number)workspace(workspace,true).get("revision")).longValue();
            Map<String,Object> conversation=conversationId==null?null:conversation(workspace,conversationId,true);
            List<Map<String,Object>> previous=db.query("SELECT * FROM tasks WHERE workspace_id=? AND idempotency_key=?",this::taskRow,workspace,key);
            if (!previous.isEmpty()) {
                Map<String,Object> task=previous.getFirst();
                if (!hash.equals(task.get("request_hash"))) throw new ApiError(HttpStatus.CONFLICT,"idempotency_conflict","This Idempotency-Key already belongs to a different request");
                metrics.counter("lablens.tasks.reused").increment();
                return new Submission(task,true);
            }
            long conversationRevision=0;
            Map<String,Object> state=null;
            if (conversation!=null) {
                conversationRevision=((Number)conversation.get("revision")).longValue();
                if (turn.expectedConversationRevision()!=conversationRevision) throw new ApiError(HttpStatus.CONFLICT,"stale_conversation","Conversation changed. Refresh its turns and state before sending a new message.");
                Integer active=db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=? AND conversation_id=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",Integer.class,workspace,conversationId);
                if (active!=null && active>0) throw new ApiError(HttpStatus.CONFLICT,"conversation_busy","This conversation already has an active turn. Wait until it finishes before sending another message.");
                @SuppressWarnings("unchecked") Map<String,Object> previousState=(Map<String,Object>)conversation.get("state");
                state=new LinkedHashMap<>(previousState);
                if (turn.state()!=null) state.putAll(turn.state().fields());
                conversationRevision++;
            }
            if (request.requiresApproval()) {
                if (request.approvedRevision()==null) throw new ApiError(HttpStatus.CONFLICT,"approval_required","Confirm sending this exact document revision to the configured model provider before submitting.");
                if (request.approvedRevision()!=revision) throw new ApiError(HttpStatus.CONFLICT,"stale_approval","Documents changed after approval. Refresh the documents and confirm the new revision before submitting.");
            }
            List<Map<String,Object>> documents=documentRows(workspace);
            if (documents.isEmpty()) throw ApiError.bad("Add at least one document before submitting an analysis");
            Integer pending=db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",Integer.class,workspace);
            if (pending!=null && pending>=maxPending) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"queue_full","Workspace task queue is full; wait for existing tasks to finish");
            if (!redis.allowSubmission(workspace)) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"rate_limited","Workspace submission rate exceeded; retry later using the same key");
            String id=UUID.randomUUID().toString();
            Map<String,Object> snapshot=new LinkedHashMap<>(request.fields());
            snapshot.remove("approved_revision"); // Consent is durable request metadata, not a Python worker field.
            snapshot.put("snapshot_id",workspace+":"+revision);
            snapshot.put("documents",documents.stream().map(d->Map.of("id",d.get("id"),"title",d.get("title"),"text",d.get("text"),"source",sourceLabel((String)d.get("source"),(String)d.get("id")))).toList());
            if (conversation!=null) {
                List<Map<String,Object>> history=conversationTurns(workspace,conversationId,20,true);
                List<Map<String,Object>> contextHistory=history.stream().map(h->Map.<String,Object>of("seq",h.get("seq"),"question",h.get("question"),"answer",h.get("answer")==null?"":h.get("answer"),"status",h.get("status"),"snapshot_id",h.get("snapshot_id"))).toList();
                snapshot.put("conversation_context",Map.of("id",conversationId,"revision",conversationRevision,"state",state,"turns",contextHistory,"omitted_turns",Math.max(0,conversationRevision-1-history.size())));
                db.update("UPDATE conversations SET revision=?,state_json=?,updated_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=?",conversationRevision,encode(state),workspace,conversationId);
            }
            db.update("INSERT INTO tasks(id,workspace_id,idempotency_key,request_hash,request_json,snapshot_revision,snapshot_json,snapshot_document_count,status,conversation_id,conversation_seq) VALUES (?,?,?,?,?,?,?,?,'QUEUED',?,?)",id,workspace,key,hash,requestJson,revision,encode(snapshot),documents.size(),conversationId,conversation==null?null:conversationRevision);
            metrics.counter("lablens.tasks.submitted").increment();
            return new Submission(task(workspace,id),false);
        });
    }
    public Map<String,Object> task(String workspace,String id) {
        List<Map<String,Object>> rows=db.query("SELECT * FROM tasks WHERE workspace_id=? AND id=?",this::taskRow,workspace,id);
        if (rows.isEmpty()) throw ApiError.missing("Task does not exist in this workspace");
        return rows.getFirst();
    }
    /** Persist intent before contacting a worker. The workspace lock serializes new turns. */
    public Map<String,Object> cancel(String workspace,String id) {
        return tx.execute(status->{
            workspace(workspace,true);
            List<Map<String,Object>> rows=db.query("SELECT * FROM tasks WHERE workspace_id=? AND id=? FOR UPDATE",this::taskRow,workspace,id);
            if (rows.isEmpty()) throw ApiError.missing("Task does not exist in this workspace");
            Map<String,Object> task=rows.getFirst();
            if ("QUEUED".equals(task.get("status"))) {
                db.update("UPDATE tasks SET status='CANCELLED',cancel_requested_at=UTC_TIMESTAMP(6),cancel_acknowledged_at=UTC_TIMESTAMP(6),finished_at=UTC_TIMESTAMP(6),error_code='task_cancelled',error_message='Cancelled before worker dispatch; zero provider calls.',execution_usage_json=?,usage_unknown=FALSE WHERE id=?",encode(WorkerResults.zeroUsage(false)),id);
                metrics.counter("lablens.tasks.cancelled","phase","queued").increment();
            } else if ("RUNNING".equals(task.get("status"))) {
                @SuppressWarnings("unchecked") Map<String,Object> request=(Map<String,Object>)task.get("request");
                boolean external=Set.of("live","agent").contains(request.get("mode")) || !"bm25".equals(request.get("strategy"));
                db.update("UPDATE tasks SET status='CANCEL_REQUESTED',cancel_requested_at=UTC_TIMESTAMP(6),error_code='cancellation_pending',error_message='Cancellation requested. Waiting for local worker acknowledgement; an in-flight provider request may still complete.',execution_usage_json=?,usage_unknown=? WHERE id=?",encode(WorkerResults.uncertainUsage(external)),external,id);
                metrics.counter("lablens.tasks.cancel_requests").increment();
            }
            return task(workspace,id);
        });
    }
    boolean cancellationRequested(String id) {
        return Boolean.TRUE.equals(db.queryForObject("SELECT status='CANCEL_REQUESTED' FROM tasks WHERE id=?",Boolean.class,id));
    }
    List<Map<String,Object>> pendingCancellations() {
        return db.query("SELECT * FROM tasks WHERE status='CANCEL_REQUESTED' ORDER BY cancel_requested_at,id LIMIT 8",this::taskRow);
    }
    boolean acknowledgeCancellation(String id,Map<String,Object> usage) {
        return db.update("UPDATE tasks SET status='CANCELLED',cancel_acknowledged_at=UTC_TIMESTAMP(6),finished_at=UTC_TIMESTAMP(6),lease_until=NULL,error_code='task_cancelled',error_message='Local worker acknowledged cancellation. Any already-sent provider request cannot be forcibly cancelled; see execution usage.',execution_usage_json=COALESCE(?,execution_usage_json),usage_unknown=CASE WHEN ? IS NULL THEN usage_unknown ELSE ? END WHERE id=? AND status='CANCEL_REQUESTED'",usage==null?null:encode(usage),usage==null?null:encode(usage),usage!=null && Boolean.TRUE.equals(usage.get("provider_usage_unknown")),id)==1;
    }
    boolean recordCancellationResult(Claimed task,String owner,Map<String,Object> usage,Map<String,Object> result,long millis) {
        return db.update("UPDATE tasks SET error_result_json=?,execution_usage_json=?,usage_unknown=?,worker_ms=? WHERE id=? AND owner=? AND status IN ('CANCEL_REQUESTED','CANCELLED')",encode(result),encode(usage),Boolean.TRUE.equals(usage.get("provider_usage_unknown")),millis,task.id(),owner)==1;
    }
    public List<Map<String,Object>> tasks(String workspace) {
        workspace(workspace,false);
        return db.query("SELECT * FROM tasks WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 100",this::taskRow,workspace);
    }
    private Map<String,Object> taskRow(ResultSet rs,int row) throws SQLException {
        Map<String,Object> t=new LinkedHashMap<>();
        for (String field:List.of("id","workspace_id","idempotency_key","request_hash","status","error_code","error_message")) t.put(field,rs.getString(field));
        t.put("snapshot_revision",rs.getLong("snapshot_revision")); t.put("snapshot_document_count",rs.getInt("snapshot_document_count"));
        t.put("conversation_id",rs.getString("conversation_id")); t.put("conversation_seq",rs.getObject("conversation_seq")); t.put("conversation_revision",rs.getObject("conversation_seq"));
        t.put("request",decode(rs.getString("request_json")));
        String result=rs.getString("result_json"); Map<String,Object> parsed=result==null?null:decode(result); t.put("result",parsed);
        String errorResult=rs.getString("error_result_json"); t.put("error_result",errorResult==null?null:decode(errorResult));
        String execution=rs.getString("execution_usage_json");
        Map<String,Object> usage=execution==null?null:decode(execution);
        if (usage==null && "SUCCEEDED".equals(rs.getString("status")) && parsed!=null) {
            try { usage=rs.getBoolean("cache_hit")?WorkerResults.zeroUsage(true):WorkerResults.usage(parsed.get("usage")); }
            catch (IllegalArgumentException e) { usage=WorkerResults.uncertainUsage(true); }
        }
        if (usage==null && "FAILED".equals(rs.getString("status"))) usage=WorkerResults.uncertainUsage(rs.getBoolean("usage_unknown"));
        t.put("execution_usage",usage);
        if (rs.getBoolean("cache_hit") && parsed!=null) {
            Object origin=parsed.containsKey("origin_usage")?parsed.get("origin_usage"):parsed.get("usage");
            t.put("origin_usage",origin);
            // Legacy cache-hit rows also expose historical usage under an explicit provenance label.
            if (!parsed.containsKey("origin_usage")) { parsed.put("origin_usage",origin); parsed.put("usage",WorkerResults.zeroUsage(true)); parsed.put("usage_scope","Current task uses zero provider calls; origin_usage belongs to the historical cached computation."); }
        }
        t.put("cache_hit",rs.getBoolean("cache_hit")); t.put("usage_unknown",rs.getBoolean("usage_unknown")); t.put("worker_ms",rs.getObject("worker_ms"));
        for (String field:List.of("created_at","started_at","finished_at","cancel_requested_at","cancel_acknowledged_at")) t.put(field,instant(rs,field));
        return t;
    }
    static String instant(ResultSet rs,String field) throws SQLException {
        var time=rs.getTimestamp(field); return time==null?null:time.toInstant().toString();
    }
    public record Claimed(String id,String workspace,long revision,String requestHash,String mode,String snapshot) {}
    Claimed claim(String owner,int leaseSeconds) {
        return claimTx.execute(status -> {
            List<Claimed> rows=db.query("SELECT id,workspace_id,snapshot_revision,request_hash,request_json,snapshot_json FROM tasks WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED",(rs,n)->new Claimed(rs.getString("id"),rs.getString("workspace_id"),rs.getLong("snapshot_revision"),rs.getString("request_hash"),(String)decode(rs.getString("request_json")).get("mode"),rs.getString("snapshot_json")));
            if (rows.isEmpty()) return null;
            Claimed task=rows.getFirst();
            db.update("UPDATE tasks SET status='RUNNING',owner=?,started_at=UTC_TIMESTAMP(6),lease_until=TIMESTAMPADD(SECOND,?,UTC_TIMESTAMP(6)) WHERE id=?",owner,leaseSeconds,task.id());
            return task;
        });
    }
    static String sourceLabel(String source,String id) { return source==null || source.isBlank()?"document:"+id:source; }
    boolean finish(Claimed task,String owner,String result,String executionUsage,boolean cacheHit,long millis) {
        Map<String,Object> usage=decode(executionUsage);
        return db.update("UPDATE tasks SET status='SUCCEEDED',result_json=?,execution_usage_json=?,usage_unknown=?,cache_hit=?,worker_ms=?,finished_at=UTC_TIMESTAMP(6),lease_until=NULL WHERE id=? AND status='RUNNING' AND owner=? AND lease_until>UTC_TIMESTAMP(6)",result,executionUsage,Boolean.TRUE.equals(usage.get("provider_usage_unknown")),cacheHit,millis,task.id(),owner)==1;
    }
    boolean fail(Claimed task,String owner,String code,String message,boolean unknown,long millis) {
        return fail(task,owner,code,message,WorkerResults.uncertainUsage(unknown),null,millis);
    }
    boolean fail(Claimed task,String owner,String code,String message,Map<String,Object> usage,Map<String,Object> errorResult,long millis) {
        return db.update("UPDATE tasks SET status='FAILED',error_code=?,error_message=?,usage_unknown=?,execution_usage_json=?,error_result_json=?,worker_ms=?,finished_at=UTC_TIMESTAMP(6),lease_until=NULL WHERE id=? AND status='RUNNING' AND owner=? AND lease_until>UTC_TIMESTAMP(6)",code,message.substring(0,Math.min(1000,message.length())),Boolean.TRUE.equals(usage.get("provider_usage_unknown")),encode(usage),errorResult==null?null:encode(errorResult),millis,task.id(),owner)==1;
    }
    int expireLeases() {
        String external="(JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.mode')) IN ('live','agent') OR JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.strategy'))<>'bm25')";
        // A range UPDATE locks secondary index entries before primary rows and can deadlock with finish().
        // Nonlocking discovery plus guarded primary-key writes uses the same lock order as completion.
        List<String> candidates=db.queryForList("SELECT id FROM tasks FORCE INDEX(task_expiry) WHERE status='RUNNING' AND lease_until<=UTC_TIMESTAMP(6) ORDER BY lease_until,id LIMIT 100",String.class);
        int expired=0;
        for (String id:candidates) expired+=db.update("UPDATE tasks SET status='FAILED',error_code='worker_interrupted',error_message='Worker lease expired. External provider usage may be unknown; this task will not be automatically retried.',usage_unknown="+external+",execution_usage_json=CASE WHEN "+external+" THEN CAST(? AS JSON) ELSE CAST(? AS JSON) END,finished_at=UTC_TIMESTAMP(6),lease_until=NULL WHERE id=? AND status='RUNNING' AND lease_until<=UTC_TIMESTAMP(6)",encode(WorkerResults.uncertainUsage(true)),encode(WorkerResults.zeroUsage(false)),id);
        return expired;
    }
    Map<String,Object> status() {
        Map<String,Object> counts=new LinkedHashMap<>();
        for (String status:List.of("QUEUED","RUNNING","CANCEL_REQUESTED","SUCCEEDED","FAILED","CANCELLED")) counts.put(status,0);
        db.query("SELECT status,COUNT(*) AS total FROM tasks GROUP BY status",rs->{counts.put(rs.getString("status"),rs.getLong("total"));});
        return Map.of("database","available","task_counts",counts,"workspace_count",db.queryForObject("SELECT COUNT(*) FROM workspaces",Long.class));
    }
}
