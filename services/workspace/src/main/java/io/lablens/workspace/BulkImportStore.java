package io.lablens.workspace;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Bounded atomic import. No provider, Redis, RabbitMQ or filesystem operations occur inside its transactions. */
@Service
public class BulkImportStore {
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final WorkspaceStore workspaces;
    private final ObjectMapper json;
    private final MeterRegistry metrics;
    private final int workspacePending;
    private final int globalPending;

    public BulkImportStore(JdbcTemplate db,TransactionTemplate template,WorkspaceStore workspaces,ObjectMapper json,MeterRegistry metrics,
                           @Value("${lablens.import-max-pending-workspace:4}") int workspacePending,
                           @Value("${lablens.import-max-pending-global:64}") int globalPending) {
        if (workspacePending<1 || workspacePending>32 || globalPending<1 || globalPending>512)
            throw new IllegalArgumentException("Import pending limits must be 1–32 per workspace and 1–512 globally");
        this.db=db; this.workspaces=workspaces; this.json=json; this.metrics=metrics;
        this.workspacePending=workspacePending; this.globalPending=globalPending;
        this.tx=new TransactionTemplate(Objects.requireNonNull(template.getTransactionManager()));
        this.tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    public record Submission(Map<String,Object> batch,boolean reused) {}

    public Submission submit(String workspace,String key,BulkImportRequests.Batch input) {
        Requests.idempotencyKey(key);
        if (input==null) throw ApiError.bad("Import request must not be null");
        BulkImportRequests.Batch request=input.normalized();
        String payload=workspaces.encode(request.fields());
        if (payload.getBytes(StandardCharsets.UTF_8).length>BulkImportRequests.MAX_REQUEST_BYTES)
            throw new ApiError(HttpStatus.PAYLOAD_TOO_LARGE,"import_payload_too_large","The encoded import request exceeds 1 MiB");
        String hash=Requests.sha256(payload);
        Submission previous=previous(workspace,key,hash);
        if (previous!=null) return previous;
        requireReadCommitted();
        return tx.execute(status->{
            // Global admission precedes the workspace lock. Apply/cancel never take this lock,
            // so completion can only free capacity while an admission check is running.
            db.queryForObject("SELECT id FROM bulk_import_admission WHERE id=1 FOR UPDATE",Integer.class);
            long revision=((Number)workspaces.workspace(workspace,true).get("revision")).longValue();
            Submission committed=previous(workspace,key,hash);
            if (committed!=null) return committed;
            if (revision!=request.expectedRevision()) throw new ApiError(HttpStatus.CONFLICT,"stale_revision","Workspace documents changed; refresh before importing");
            long local=count("SELECT COUNT(*) FROM bulk_imports WHERE workspace_id=? AND status='QUEUED'",workspace);
            if (local>=workspacePending) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"import_queue_full","Workspace import queue is full; retry with the same key");
            long global=count("SELECT COUNT(*) FROM bulk_imports WHERE status='QUEUED'");
            if (global>=globalPending) throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"import_global_queue_full","Global import queue is full; retry with the same key");
            String id=UUID.randomUUID().toString();
            db.update("INSERT INTO bulk_imports(id,workspace_id,idempotency_key,request_hash,request_json,expected_revision,document_count,status,result_json) VALUES (?,?,?,?,?,?,?,'QUEUED',?)",
                id,workspace,key,hash,payload,request.expectedRevision(),request.documents().size(),items(request,"QUEUED",null));
            return new Submission(batch(workspace,id,false),false);
        });
    }

    public Map<String,Object> get(String workspace,String id) { return batch(workspace,id,false); }

    public Map<String,Object> cancel(String workspace,String id) {
        requireReadCommitted();
        return tx.execute(status->{
            workspaces.workspace(workspace,true);
            Map<String,Object> batch=batch(workspace,id,true);
            if ("QUEUED".equals(batch.get("status"))) {
                BulkImportRequests.Batch request=request(workspace,id);
                db.update("UPDATE bulk_imports SET status='CANCELLED',result_json=?,finished_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=? AND status='QUEUED'",
                    items(request,"CANCELLED","cancelled_before_apply"),workspace,id);
            }
            return batch(workspace,id,false);
        });
    }

    /** Process at most one persisted candidate. Interrupted/failed SQL rolls back both documents and state. */
    public boolean processNext() {
        // Failure recording must start after the apply transaction has rolled back,
        // rather than accidentally joining an outer rollback-only transaction.
        if (TransactionSynchronizationManager.isActualTransactionActive())
            throw new IllegalStateException("Import polling must own its transaction boundaries");
        List<Map<String,Object>> candidates=db.queryForList("SELECT id,workspace_id FROM bulk_imports WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1");
        if (candidates.isEmpty()) return false;
        Map<String,Object> next=candidates.getFirst();
        String workspace=(String)next.get("workspace_id"),id=(String)next.get("id");
        try { return apply(workspace,id); }
        catch (DataIntegrityViolationException e) {
            // A permanent constraint failure must not poison the head of the queue.
            // Connection loss, lock timeouts and other transient failures propagate,
            // retaining QUEUED for the next poll instead of losing an accepted batch.
            permanentFailure(workspace,id,"import_integrity_error","The atomic import violated a database constraint; no documents from this attempt were committed");
            return false;
        } catch (InvalidImportPayload e) {
            permanentFailure(workspace,id,"import_invalid_payload","The persisted import payload is invalid; no documents from this attempt were committed");
            return false;
        }
    }

    boolean apply(String workspace,String id) {
        requireReadCommitted();
        Boolean applied=tx.execute(status->{
            // Every document mutation uses this same workspace lock. Read the batch only
            // after acquiring it, matching cancel and avoiding batch/workspace lock inversion.
            long current=((Number)workspaces.workspace(workspace,true).get("revision")).longValue();
            Map<String,Object> batch=batch(workspace,id,true);
            if (!"QUEUED".equals(batch.get("status"))) return false;
            BulkImportRequests.Batch request=request(workspace,id);
            if (current!=request.expectedRevision()) {
                fail(workspace,id,request,"stale_revision","Workspace changed while the import was queued");
                return false;
            }
            List<Map<String,Object>> existing=db.queryForList("SELECT id,body FROM documents WHERE workspace_id=?",workspace);
            var lengths=new LinkedHashMap<String,Integer>();
            for (Map<String,Object> document:existing) lengths.put((String)document.get("id"),((String)document.get("body")).length());
            for (BulkImportRequests.Document document:request.documents()) lengths.put(document.id(),document.text().length());
            long characters=lengths.values().stream().mapToLong(Integer::longValue).sum();
            if (lengths.size()>WorkspaceStore.MAX_DOCUMENTS || characters>WorkspaceStore.MAX_CHARACTERS) {
                fail(workspace,id,request,"workspace_capacity","Applying the batch would exceed 100 documents or 200000 text characters");
                return false;
            }
            long revision=Math.addExact(current,1);
            for (BulkImportRequests.Document document:request.documents()) {
                db.update("INSERT INTO documents(workspace_id,id,title,body,source,version) VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE title=?,body=?,source=?,version=?,updated_at=UTC_TIMESTAMP(6)",
                    workspace,document.id(),document.title(),document.text(),document.source(),revision,document.title(),document.text(),document.source(),revision);
                db.update("INSERT INTO document_versions(workspace_id,document_id,revision,title,body,source) VALUES (?,?,?,?,?,?)",
                    workspace,document.id(),revision,document.title(),document.text(),document.source());
            }
            db.update("UPDATE workspaces SET revision=? WHERE id=?",revision,workspace);
            db.update("UPDATE bulk_imports SET status='APPLIED',applied_revision=?,result_json=?,finished_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=? AND status='QUEUED'",
                revision,items(request,"APPLIED",null),workspace,id);
            return true;
        });
        if (Boolean.TRUE.equals(applied)) metrics.counter("lablens.imports.applied").increment();
        return Boolean.TRUE.equals(applied);
    }

    private void fail(String workspace,String id,BulkImportRequests.Batch request,String code,String message) {
        db.update("UPDATE bulk_imports SET status='FAILED',result_json=?,error_code=?,error_message=?,finished_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=? AND status='QUEUED'",
            items(request,"FAILED",code),code,message,workspace,id);
    }
    private void permanentFailure(String workspace,String id,String code,String message) {
        tx.executeWithoutResult(status->{
            workspaces.workspace(workspace,true);
            Map<String,Object> batch=batch(workspace,id,true);
            // Cancellation or another successful poll may have won after rollback.
            if (!"QUEUED".equals(batch.get("status"))) return;
            var failedItems=new ArrayList<Map<String,Object>>();
            for (Object value:(List<?>)batch.get("items")) {
                var item=(Map<?,?>)value;
                failedItems.add(Map.of("id",item.get("id"),"status","FAILED","reason",code));
            }
            db.update("UPDATE bulk_imports SET status='FAILED',result_json=?,error_code=?,error_message=?,finished_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=? AND status='QUEUED'",
                workspaces.encode(Map.of("items",failedItems)),code,message,workspace,id);
        });
    }
    private String items(BulkImportRequests.Batch request,String state,String reason) {
        var result=new ArrayList<Map<String,Object>>();
        for (BulkImportRequests.Document document:request.documents()) {
            Map<String,Object> item=new LinkedHashMap<>(); item.put("id",document.id()); item.put("status",state); item.put("reason",reason); result.add(item);
        }
        return workspaces.encode(Map.of("items",result));
    }
    private BulkImportRequests.Batch request(String workspace,String id) {
        String value=db.queryForObject("SELECT request_json FROM bulk_imports WHERE workspace_id=? AND id=?",String.class,workspace,id);
        try {
            BulkImportRequests.Batch parsed=json.readValue(value,BulkImportRequests.Batch.class);
            if (parsed==null) throw new InvalidImportPayload();
            return parsed.normalized();
        } catch (JsonProcessingException | ApiError e) { throw new InvalidImportPayload(); }
    }
    private static class InvalidImportPayload extends RuntimeException {}
    private Submission previous(String workspace,String key,String hash) {
        List<Map<String,Object>> rows=db.queryForList("SELECT id,request_hash FROM bulk_imports WHERE workspace_id=? AND idempotency_key=?",workspace,key);
        if (rows.isEmpty()) return null;
        if (!hash.equals(rows.getFirst().get("request_hash"))) throw new ApiError(HttpStatus.CONFLICT,"idempotency_conflict","Idempotency-Key already belongs to a different import request");
        return new Submission(batch(workspace,(String)rows.getFirst().get("id"),false),true);
    }
    private Map<String,Object> batch(String workspace,String id,boolean lock) {
        List<Map<String,Object>> rows=db.query("SELECT id,workspace_id,status,expected_revision,applied_revision,document_count,result_json,error_code,error_message,created_at,finished_at FROM bulk_imports WHERE workspace_id=? AND id=?"+(lock?" FOR UPDATE":""),this::row,workspace,id);
        if (rows.isEmpty()) throw ApiError.missing("Import batch does not exist in this workspace");
        return rows.getFirst();
    }
    private Map<String,Object> row(ResultSet rs,int ignored) throws SQLException {
        Map<String,Object> result=new LinkedHashMap<>();
        for (String field:List.of("id","workspace_id","status","error_code","error_message")) result.put(field,rs.getString(field));
        result.put("expected_revision",rs.getLong("expected_revision"));
        long applied=rs.getLong("applied_revision"); result.put("applied_revision",rs.wasNull()?null:applied);
        result.put("document_count",rs.getInt("document_count"));
        result.put("items",workspaces.decode(rs.getString("result_json")).get("items"));
        result.put("index_status","not_checked");
        for (String field:List.of("created_at","finished_at")) {
            var value=rs.getTimestamp(field); result.put(field,value==null?null:value.toInstant().toString());
        }
        return result;
    }
    private long count(String sql,Object...args) { return Objects.requireNonNull(db.queryForObject(sql,Long.class,args)); }
    private void requireReadCommitted() {
        if (TransactionSynchronizationManager.isActualTransactionActive()
            && !Objects.equals(TransactionSynchronizationManager.getCurrentTransactionIsolationLevel(),TransactionDefinition.ISOLATION_READ_COMMITTED))
            throw new IllegalStateException("Bulk import requires READ_COMMITTED when joining an existing transaction");
    }
}
