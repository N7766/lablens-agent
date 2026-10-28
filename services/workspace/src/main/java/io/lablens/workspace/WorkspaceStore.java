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
    public Submission submit(String workspace,String key,Requests.Task input) {
        Requests.idempotencyKey(key);
        Requests.Task request=input.normalized();
        String requestJson=encode(request.fields());
        String hash=Requests.sha256(requestJson);
        return tx.execute(status -> {
            long revision=((Number)workspace(workspace,true).get("revision")).longValue();
            List<Map<String,Object>> previous=db.query("SELECT * FROM tasks WHERE workspace_id=? AND idempotency_key=?",this::taskRow,workspace,key);
            if (!previous.isEmpty()) {
                Map<String,Object> task=previous.getFirst();
                if (!hash.equals(task.get("request_hash"))) throw new ApiError(HttpStatus.CONFLICT,"idempotency_conflict","This Idempotency-Key already belongs to a different request");
                metrics.counter("lablens.tasks.reused").increment();
                return new Submission(task,true);
            }
            List<Map<String,Object>> documents=documentRows(workspace);
            if (documents.isEmpty()) throw ApiError.bad("Add at least one document before submitting an analysis");
            Integer pending=db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=? AND status IN ('QUEUED','RUNNING')",Integer.class,workspace);
            if (pending!=null && pending>=maxPending) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"queue_full","Workspace task queue is full; wait for existing tasks to finish");
            if (!redis.allowSubmission(workspace)) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"rate_limited","Workspace submission rate exceeded; retry later using the same key");
            String id=UUID.randomUUID().toString();
            Map<String,Object> snapshot=new LinkedHashMap<>(request.fields());
            snapshot.put("snapshot_id",workspace+":"+revision);
            snapshot.put("documents",documents.stream().map(d->Map.of("id",d.get("id"),"title",d.get("title"),"text",d.get("text"),"source",sourceLabel((String)d.get("source"),(String)d.get("id")))).toList());
            db.update("INSERT INTO tasks(id,workspace_id,idempotency_key,request_hash,request_json,snapshot_revision,snapshot_json,snapshot_document_count,status) VALUES (?,?,?,?,?,?,?,?,'QUEUED')",id,workspace,key,hash,requestJson,revision,encode(snapshot),documents.size());
            metrics.counter("lablens.tasks.submitted").increment();
            return new Submission(task(workspace,id),false);
        });
    }
    public Map<String,Object> task(String workspace,String id) {
        List<Map<String,Object>> rows=db.query("SELECT * FROM tasks WHERE workspace_id=? AND id=?",this::taskRow,workspace,id);
        if (rows.isEmpty()) throw ApiError.missing("Task does not exist in this workspace");
        return rows.getFirst();
    }
    public List<Map<String,Object>> tasks(String workspace) {
        workspace(workspace,false);
        return db.query("SELECT * FROM tasks WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 100",this::taskRow,workspace);
    }
    private Map<String,Object> taskRow(ResultSet rs,int row) throws SQLException {
        Map<String,Object> t=new LinkedHashMap<>();
        for (String field:List.of("id","workspace_id","idempotency_key","request_hash","status","error_code","error_message")) t.put(field,rs.getString(field));
        t.put("snapshot_revision",rs.getLong("snapshot_revision")); t.put("snapshot_document_count",rs.getInt("snapshot_document_count"));
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
        for (String field:List.of("created_at","started_at","finished_at")) t.put(field,instant(rs,field));
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
        String external="(JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.mode'))='live' OR JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.strategy'))<>'bm25')";
        // A range UPDATE locks secondary index entries before primary rows and can deadlock with finish().
        // Nonlocking discovery plus guarded primary-key writes uses the same lock order as completion.
        List<String> candidates=db.queryForList("SELECT id FROM tasks FORCE INDEX(task_expiry) WHERE status='RUNNING' AND lease_until<=UTC_TIMESTAMP(6) ORDER BY lease_until,id LIMIT 100",String.class);
        int expired=0;
        for (String id:candidates) expired+=db.update("UPDATE tasks SET status='FAILED',error_code='worker_interrupted',error_message='Worker lease expired. External provider usage may be unknown; this task will not be automatically retried.',usage_unknown="+external+",execution_usage_json=CASE WHEN "+external+" THEN CAST(? AS JSON) ELSE CAST(? AS JSON) END,finished_at=UTC_TIMESTAMP(6),lease_until=NULL WHERE id=? AND status='RUNNING' AND lease_until<=UTC_TIMESTAMP(6)",encode(WorkerResults.uncertainUsage(true)),encode(WorkerResults.zeroUsage(false)),id);
        return expired;
    }
    Map<String,Object> status() {
        Map<String,Object> counts=new LinkedHashMap<>();
        for (String status:List.of("QUEUED","RUNNING","SUCCEEDED","FAILED")) counts.put(status,0);
        db.query("SELECT status,COUNT(*) AS total FROM tasks GROUP BY status",rs->{counts.put(rs.getString("status"),rs.getLong("total"));});
        return Map.of("database","available","task_counts",counts,"workspace_count",db.queryForObject("SELECT COUNT(*) FROM workspaces",Long.class));
    }
}
