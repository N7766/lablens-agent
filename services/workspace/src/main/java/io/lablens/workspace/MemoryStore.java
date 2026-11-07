package io.lablens.workspace;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/** Explicit shared annotations. Workspace locking serializes document, memory and task snapshots. */
@Service
public class MemoryStore {
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final AuthStore auth;
    public MemoryStore(JdbcTemplate db,TransactionTemplate tx,AuthStore auth) { this.db=db;this.tx=tx;this.auth=auth; }
    private Map<String,Object> lock(String workspace) {
        var rows=db.queryForList("SELECT revision,memory_revision FROM workspaces WHERE id=? FOR UPDATE",workspace);
        if (rows.isEmpty()) throw ApiError.missing("Workspace does not exist");
        return rows.getFirst();
    }
    private void writer(String workspace,AuthStore.Identity identity) {
        if (identity==null) throw AuthStore.unauthorized();
        auth.access(workspace,identity,true,false);
    }
    public Map<String,Object> list(String workspace) {
        return tx.execute(status->{
            var w=lock(workspace);
            return Map.of("workspace_id",workspace,"revision",w.get("memory_revision"),"memories",rows(db,workspace));
        });
    }
    public Map<String,Object> write(String workspace,String id,MemoryRequests.Mutation input,AuthStore.Identity identity) {
        boolean update=id!=null;
        if (update) MemoryRequests.id(id);
        var request=input.normalized(update);
        return tx.execute(status->{
            var w=lock(workspace); writer(workspace,identity);
            long revision=((Number)w.get("memory_revision")).longValue();
            expectedRevision(request.expectedRevision(),revision);
            var existing=rows(db,workspace);
            Map<String,Object> previous=update?existing.stream().filter(m->id.equals(m.get("id"))).findFirst().orElseThrow(()->ApiError.missing("Memory does not exist")):null;
            if (update && request.expectedVersion()!=((Number)previous.get("version")).longValue()) throw conflict("stale_memory","Memory changed. Refresh before updating.");
            for (var row:existing) if (request.key().equals(row.get("key")) && !Objects.equals(id,row.get("id")))
                throw conflict("memory_conflict","A memory with this key already exists: "+row.get("id")+". Review it and explicitly update its version.");
            if (!update && existing.size()>=100) throw new ApiError(HttpStatus.PAYLOAD_TOO_LARGE,"memory_capacity","Workspace allows at most 100 stored memories, including stale and expired entries. Delete one before adding another.");
            var documents=db.queryForList("SELECT body,version FROM documents WHERE workspace_id=? AND id=?",workspace,request.documentId());
            if (documents.isEmpty()) throw conflict("memory_source_stale","The source document no longer exists.");
            var document=documents.getFirst(); String body=(String)document.get("body");
            if (((Number)document.get("version")).longValue()!=request.documentVersion()) throw conflict("memory_source_stale","The source document version changed. Refresh and select a current quote.");
            if (!body.contains(request.quote())) throw ApiError.bad("quote must be an exact substring of the current source document");
            Instant now=db.queryForObject("SELECT UTC_TIMESTAMP(6)",Timestamp.class).toInstant();
            Timestamp expiry=null;
            if (request.expiresAt()!=null) {
                Instant value=Instant.parse(request.expiresAt());
                if (!value.isAfter(now) || value.isAfter(now.plus(365,ChronoUnit.DAYS))) throw ApiError.bad("expires_at must be in the future and no more than 365 days away");
                expiry=Timestamp.from(value);
            }
            String memoryId=update?id:UUID.randomUUID().toString();
            String sourceSnapshot=workspace+":"+w.get("revision");
            long version=update?((Number)previous.get("version")).longValue()+1:1;
            if (update) db.update("UPDATE workspace_memories SET memory_key=?,version=?,document_id=?,document_version=?,document_sha256=?,snapshot_id=?,source_quote=?,note=?,expires_at=?,updated_at=UTC_TIMESTAMP(6) WHERE workspace_id=? AND id=?",
                request.key(),version,request.documentId(),request.documentVersion(),Requests.sha256(body),sourceSnapshot,request.quote(),request.note(),expiry,workspace,memoryId);
            else db.update("INSERT INTO workspace_memories(id,workspace_id,memory_key,version,created_by,document_id,document_version,document_sha256,snapshot_id,source_quote,note,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                memoryId,workspace,request.key(),version,identity.id(),request.documentId(),request.documentVersion(),Requests.sha256(body),sourceSnapshot,request.quote(),request.note(),expiry);
            db.update("UPDATE workspaces SET memory_revision=memory_revision+1 WHERE id=?",workspace);
            return Map.of("workspace_id",workspace,"revision",revision+1,"memory",rows(db,workspace).stream().filter(m->memoryId.equals(m.get("id"))).findFirst().orElseThrow());
        });
    }
    public Map<String,Object> delete(String workspace,String id,MemoryRequests.Delete request,AuthStore.Identity identity) {
        MemoryRequests.id(id); request.validate();
        return tx.execute(status->{
            var w=lock(workspace); writer(workspace,identity);
            long revision=((Number)w.get("memory_revision")).longValue(); expectedRevision(request.expectedRevision(),revision);
            var versions=db.queryForList("SELECT version FROM workspace_memories WHERE workspace_id=? AND id=?",Long.class,workspace,id);
            if (versions.isEmpty()) throw ApiError.missing("Memory does not exist");
            if (!request.expectedVersion().equals(versions.getFirst())) throw conflict("stale_memory","Memory changed. Refresh before deleting.");
            db.update("DELETE FROM workspace_memories WHERE workspace_id=? AND id=?",workspace,id);
            db.update("UPDATE workspaces SET memory_revision=memory_revision+1 WHERE id=?",workspace);
            return Map.of("workspace_id",workspace,"revision",revision+1,"deleted_id",id);
        });
    }
    private static void expectedRevision(long expected,long actual) {
        if (expected!=actual) throw conflict("stale_memory_revision","Workspace memories changed. Refresh the list and approve the new revision.");
    }
    private static ApiError conflict(String code,String message) { return new ApiError(HttpStatus.CONFLICT,code,message); }
    static List<Map<String,Object>> rows(JdbcTemplate db,String workspace) {
        return db.query("SELECT m.*,d.body AS current_body,d.version AS current_version,UTC_TIMESTAMP(6) AS observed_at FROM workspace_memories m LEFT JOIN documents d ON d.workspace_id=m.workspace_id AND d.id=m.document_id WHERE m.workspace_id=? ORDER BY m.memory_key,m.id",MemoryStore::row,workspace);
    }
    private static Map<String,Object> row(ResultSet rs,int ignored) throws SQLException {
        String body=rs.getString("current_body");
        Timestamp expiry=rs.getTimestamp("expires_at");
        String status=expiry!=null && !expiry.after(rs.getTimestamp("observed_at"))?"expired":body==null || rs.getLong("current_version")!=rs.getLong("document_version") || !Requests.sha256(body).equals(rs.getString("document_sha256")) || !body.contains(rs.getString("source_quote"))?"stale":"active";
        Map<String,Object> result=new LinkedHashMap<>();
        for (String field:List.of("id","created_by")) result.put(field,rs.getString(field));
        result.put("key",rs.getString("memory_key")); result.put("version",rs.getLong("version")); result.put("scope","workspace"); result.put("status",status);
        result.put("source",Map.of("document_id",rs.getString("document_id"),"document_version",rs.getLong("document_version"),"document_sha256",rs.getString("document_sha256"),"snapshot_id",rs.getString("snapshot_id"),"quote",rs.getString("source_quote")));
        result.put("note",rs.getString("note"));
        for (String field:List.of("expires_at","created_at","updated_at")) result.put(field,WorkspaceStore.instant(rs,field));
        return result;
    }
    /** Caller already holds the workspace row lock, after the global admission lock. */
    static Map<String,Object> selected(JdbcTemplate db,String workspace,Requests.Task request) {
        if (request.memoryIds()==null || request.memoryIds().isEmpty()) return null;
        if (request.approvedMemoryRevision()==null) throw conflict("memory_approval_required","Approve the exact selected workspace memory revision before sending it to the model.");
        long revision=db.queryForObject("SELECT memory_revision FROM workspaces WHERE id=?",Long.class,workspace);
        expectedRevision(request.approvedMemoryRevision(),revision);
        var available=rows(db,workspace); var entries=new ArrayList<Map<String,Object>>();
        for (String id:request.memoryIds()) {
            var memory=available.stream().filter(m->id.equals(m.get("id"))).findFirst().orElseThrow(()->conflict("memory_unavailable","A selected memory does not exist in this workspace."));
            if (!"active".equals(memory.get("status"))) throw conflict("memory_unavailable","A selected memory is stale or expired. Refresh and explicitly replace or remove it.");
            entries.add(memory);
        }
        var context=Map.<String,Object>of("workspace_id",workspace,"revision",revision,"entries",entries);
        if (MemoryRequests.canonical(context).getBytes(java.nio.charset.StandardCharsets.UTF_8).length>65536)
            throw new ApiError(HttpStatus.PAYLOAD_TOO_LARGE,"memory_context_capacity","Selected memory context exceeds 64 KiB. Select fewer or shorter entries.");
        return context;
    }
}
