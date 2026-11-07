package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;

/** Genuine MySQL tests. No H2 or mock transaction implementation. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.import-dispatch-enabled=false","lablens.cache-namespace=bulk-import-integration"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class BulkImportIntegrationTest {
    @Autowired BulkImportStore imports;
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired TransactionTemplate tx;
    @Autowired ObjectMapper json;
    @Autowired MeterRegistry metrics;
    @Autowired MockMvc mvc;
    private final List<String> workspaces=new ArrayList<>();
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("import-test-"+UUID.randomUUID())).get("id");
        workspaces.add(id); return id;
    }
    private BulkImportRequests.Batch request(long revision,String...ids) {
        return new BulkImportRequests.Batch(java.util.Arrays.stream(ids).map(id->new BulkImportRequests.Document(id,"Title "+id,"Evidence for "+id,null)).toList(),revision);
    }
    private String submit(String workspace,String key,BulkImportRequests.Batch request) { return (String)imports.submit(workspace,key,request).batch().get("id"); }
    private int documents(String workspace) { return db.queryForObject("SELECT COUNT(*) FROM documents WHERE workspace_id=?",Integer.class,workspace); }
    private long revision(String workspace) { return ((Number)store.documents(workspace).get("revision")).longValue(); }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            db.update("DELETE FROM bulk_imports WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }
    @Test void batchAppliesAtomicallyAtOneRevisionAndDoesNotClaimIndexReadiness() {
        String workspace=workspace(),id=submit(workspace,"batch",request(0,"b","a"));
        assertEquals("QUEUED",imports.get(workspace,id).get("status")); assertEquals(0,documents(workspace));
        assertTrue(imports.apply(workspace,id));
        Map<String,Object> result=imports.get(workspace,id);
        assertEquals("APPLIED",result.get("status")); assertEquals(1L,result.get("applied_revision"));
        assertEquals("not_checked",result.get("index_status")); assertEquals(2,documents(workspace)); assertEquals(1,revision(workspace));
        assertEquals(List.of(1L,1L),db.queryForList("SELECT version FROM documents WHERE workspace_id=? ORDER BY id",Long.class,workspace));
        assertEquals(2,db.queryForObject("SELECT COUNT(*) FROM document_versions WHERE workspace_id=? AND revision=1",Integer.class,workspace));
        assertEquals("document:a",store.document(workspace,"a").get("source"));
        assertFalse(imports.apply(workspace,id)); assertEquals(1,revision(workspace));
        assertEquals("APPLIED",imports.cancel(workspace,id).get("status"));
    }
    @Test void concurrentIdenticalSubmissionsCreateExactlyOneDurableBatch() throws Exception {
        String workspace=workspace();
        try (var pool=Executors.newFixedThreadPool(12)) {
            var gate=new CountDownLatch(1); var futures=new ArrayList<Future<BulkImportStore.Submission>>();
            for (int i=0;i<12;i++) futures.add(pool.submit(()->{gate.await(); return imports.submit(workspace,"same",request(0,"a","b"));}));
            gate.countDown(); var ids=new HashSet<Object>(); int created=0;
            for (var future:futures) { var result=future.get(15,TimeUnit.SECONDS); ids.add(result.batch().get("id")); if (!result.reused()) created++; }
            assertEquals(1,created); assertEquals(1,ids.size());
            assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM bulk_imports WHERE workspace_id=?",Integer.class,workspace));
        }
    }
    @Test void replaySurvivesMutationButKeyConflictAndNewStaleSubmissionAreRejected() {
        String workspace=workspace(); var request=request(0,"a"); String id=submit(workspace,"key",request);
        assertTrue(imports.apply(workspace,id)); store.putDocument(workspace,"extra",new Requests.Document("Extra","Mutation",null));
        var replay=imports.submit(workspace,"key",request); assertTrue(replay.reused()); assertEquals(id,replay.batch().get("id"));
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->imports.submit(workspace,"key",request(0,"other"))).code);
        assertEquals("stale_revision",assertThrows(ApiError.class,()->imports.submit(workspace,"new",request)).code);
        assertEquals(2,revision(workspace));
    }
    @Test void staleQueuedBatchFailsEveryItemWithoutPartialWrites() {
        String workspace=workspace(); String first=submit(workspace,"first",request(0,"first")),second=submit(workspace,"second",request(0,"x","y"));
        assertTrue(imports.apply(workspace,first)); assertFalse(imports.apply(workspace,second));
        var result=imports.get(workspace,second); assertEquals("FAILED",result.get("status")); assertEquals("stale_revision",result.get("error_code"));
        assertNull(result.get("applied_revision")); assertEquals(1,documents(workspace)); assertEquals(1,revision(workspace));
        for (Object item:(List<?>)result.get("items")) { assertEquals("FAILED",((Map<?,?>)item).get("status")); assertEquals("stale_revision",((Map<?,?>)item).get("reason")); }
    }
    @Test void capacityFailureRollsBackAllItemsIncludingAnOtherwiseValidReplacement() {
        String workspace=workspace();
        for (int i=0;i<10;i++) store.putDocument(workspace,"d"+i,new Requests.Document("Large","x".repeat(20000),null));
        var replacement=new BulkImportRequests.Document("d0","Replaced","y".repeat(20000),null);
        var extra=new BulkImportRequests.Document("extra","Extra","one more",null);
        String id=submit(workspace,"full",new BulkImportRequests.Batch(List.of(replacement,extra),10L));
        assertFalse(imports.apply(workspace,id)); assertEquals("workspace_capacity",imports.get(workspace,id).get("error_code"));
        assertEquals("Large",store.document(workspace,"d0").get("title")); assertEquals(10,documents(workspace)); assertEquals(10,revision(workspace));
        String replacementOnly=submit(workspace,"replace",new BulkImportRequests.Batch(List.of(replacement),10L));
        assertTrue(imports.apply(workspace,replacementOnly)); assertEquals(10,documents(workspace)); assertEquals(11,revision(workspace));
    }
    @Test void injectedSqlFailureRollsBackThenFreshStoreResumesQueuedBatch() {
        String workspace=workspace(),id=submit(workspace,"rollback",request(0,"first","fail"));
        // A deliberate history-key collision makes MySQL reject the second item after
        // the first item's writes. This needs no administrative trigger privileges.
        db.update("INSERT INTO document_versions(workspace_id,document_id,revision,title,body,source) VALUES (?,'fail',1,'Injected fault','Collision','test://fault')",workspace);
        try {
            assertThrows(DataAccessException.class,()->imports.apply(workspace,id));
            assertEquals(0,documents(workspace)); assertEquals(0,revision(workspace));
            assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM document_versions WHERE workspace_id=? AND document_id='first'",Integer.class,workspace));
            assertEquals("QUEUED",imports.get(workspace,id).get("status"));
        } finally { db.update("DELETE FROM document_versions WHERE workspace_id=? AND document_id='fail' AND revision=1",workspace); }
        var reopened=new BulkImportStore(db,tx,store,json,metrics,4,64);
        assertTrue(reopened.apply(workspace,id)); assertEquals(2,documents(workspace)); assertEquals(1,revision(workspace));
        assertEquals("APPLIED",reopened.get(workspace,id).get("status"));
    }
    @Test void cancellationIsDurableIdempotentAndNeverWritesDocuments() {
        String workspace=workspace(),id=submit(workspace,"cancel",request(0,"a","b"));
        assertEquals("CANCELLED",imports.cancel(workspace,id).get("status"));
        assertEquals("CANCELLED",imports.cancel(workspace,id).get("status")); assertFalse(imports.apply(workspace,id));
        assertEquals(0,documents(workspace)); assertEquals(0,revision(workspace));
        assertTrue(imports.submit(workspace,"cancel",request(0,"a","b")).reused());
        for (Object item:(List<?>)imports.get(workspace,id).get("items")) assertEquals("cancelled_before_apply",((Map<?,?>)item).get("reason"));
    }
    @Test void newlyStartedPollerReadsDurableQueueAndDisabledPollerLeavesItAlone() {
        String workspace=workspace(),id=submit(workspace,"poll",request(0,"a","b"));
        new BulkImportDispatcher(imports,false).poll(); assertEquals("QUEUED",imports.get(workspace,id).get("status"));
        new BulkImportDispatcher(new BulkImportStore(db,tx,store,json,metrics,4,64),true).poll();
        assertEquals("APPLIED",imports.get(workspace,id).get("status")); assertEquals(2,documents(workspace)); assertEquals(1,revision(workspace));
    }
    @Test void permanentSqlFailureDoesNotStarveTheNextQueuedBatch() {
        String workspace=workspace(),other=workspace();
        String poison=submit(workspace,"poison",request(0,"first","fail"));
        db.update("UPDATE bulk_imports SET created_at=TIMESTAMPADD(SECOND,-60,UTC_TIMESTAMP(6)) WHERE id=?",poison);
        String healthy=submit(other,"healthy",request(0,"next"));
        db.update("INSERT INTO document_versions(workspace_id,document_id,revision,title,body,source) VALUES (?,'fail',1,'Injected fault','Collision','test://fault')",workspace);
        assertFalse(imports.processNext());
        assertEquals("FAILED",imports.get(workspace,poison).get("status"));
        assertEquals("import_integrity_error",imports.get(workspace,poison).get("error_code"));
        assertEquals(0,documents(workspace)); assertEquals(0,revision(workspace));
        for (Object item:(List<?>)imports.get(workspace,poison).get("items")) {
            assertEquals("FAILED",((Map<?,?>)item).get("status")); assertEquals("import_integrity_error",((Map<?,?>)item).get("reason"));
        }
        assertTrue(imports.processNext()); assertEquals("APPLIED",imports.get(other,healthy).get("status"));
        assertEquals(1,documents(other));
    }
    @Test void malformedPersistedPayloadFailsExplicitlyWithoutBlockingOtherImports() {
        String workspace=workspace(),other=workspace();
        String poison=submit(workspace,"poison",request(0,"first"));
        db.update("UPDATE bulk_imports SET request_json='{}',created_at=TIMESTAMPADD(SECOND,-60,UTC_TIMESTAMP(6)) WHERE id=?",poison);
        String healthy=submit(other,"healthy",request(0,"next"));
        assertFalse(imports.processNext());
        assertEquals("FAILED",imports.get(workspace,poison).get("status"));
        assertEquals("import_invalid_payload",imports.get(workspace,poison).get("error_code"));
        assertEquals(0,documents(workspace)); assertEquals(0,revision(workspace));
        assertTrue(imports.processNext()); assertEquals("APPLIED",imports.get(other,healthy).get("status"));
    }
    @Test void concurrentApplyAndCancelAlwaysYieldWholeBatchOrNoBatch() throws Exception {
        String workspace=workspace(),id=submit(workspace,"race",request(0,"a","b","c"));
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1);
            var apply=pool.submit(()->{gate.await(); return imports.apply(workspace,id);});
            var cancel=pool.submit(()->{gate.await(); return imports.cancel(workspace,id);});
            gate.countDown(); apply.get(10,TimeUnit.SECONDS); cancel.get(10,TimeUnit.SECONDS);
        }
        String status=(String)imports.get(workspace,id).get("status");
        assertTrue(List.of("APPLIED","CANCELLED").contains(status));
        assertEquals("APPLIED".equals(status)?3:0,documents(workspace)); assertEquals("APPLIED".equals(status)?1:0,revision(workspace));
    }
    @Test void pendingAdmissionIsBoundedAndReplayPrecedesQueueLimit() throws Exception {
        String a=workspace(),b=workspace(),c=workspace(); var limited=new BulkImportStore(db,tx,store,json,metrics,1,2);
        var first=limited.submit(a,"one",request(0,"a"));
        assertTrue(limited.submit(a,"one",request(0,"a")).reused());
        assertEquals("import_queue_full",assertThrows(ApiError.class,()->limited.submit(a,"two",request(0,"a"))).code);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1); var results=new ArrayList<Future<String>>();
            for (String workspace:List.of(b,c)) results.add(pool.submit(()->{
                gate.await(); try { limited.submit(workspace,"one",request(0,"a")); return "accepted"; } catch (ApiError e) { return e.code; }
            }));
            gate.countDown(); var codes=new ArrayList<String>(); for (var result:results) codes.add(result.get(10,TimeUnit.SECONDS));
            assertEquals(1,codes.stream().filter("accepted"::equals).count()); assertEquals(1,codes.stream().filter("import_global_queue_full"::equals).count());
        }
        limited.cancel(a,(String)first.batch().get("id"));
        assertFalse(limited.submit(a,"after-cancel",request(0,"a")).reused());
    }
    @Test void workspaceIsolationHttpValidationAndPerItemLifecycleAreVisible() throws Exception {
        String a=workspace(),b=workspace();
        String body="{\"expected_revision\":0,\"documents\":[{\"id\":\"doc\",\"title\":\"Title\",\"text\":\"Evidence\"}]}";
        String response=mvc.perform(post("/api/workspaces/"+a+"/imports").header("Idempotency-Key","http").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isAccepted()).andExpect(jsonPath("status").value("QUEUED")).andExpect(jsonPath("items[0].status").value("QUEUED"))
            .andExpect(jsonPath("index_status").value("not_checked")).andExpect(jsonPath("reused").value(false)).andReturn().getResponse().getContentAsString();
        String id=json.readTree(response).get("id").asText();
        mvc.perform(post("/api/workspaces/"+a+"/imports").header("Idempotency-Key","http").contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isOk()).andExpect(jsonPath("reused").value(true));
        mvc.perform(get("/api/workspaces/"+b+"/imports/"+id)).andExpect(status().isNotFound());
        mvc.perform(post("/api/workspaces/"+b+"/imports/"+id+"/cancel")).andExpect(status().isNotFound());
        mvc.perform(get("/api/workspaces/"+a+"/imports/"+id)).andExpect(status().isOk()).andExpect(jsonPath("document_count").value(1));
        mvc.perform(post("/api/workspaces/"+a+"/imports").header("Idempotency-Key","invalid").contentType(MediaType.APPLICATION_JSON).content("{\"documents\":[],\"expected_revision\":0}")).andExpect(status().isBadRequest());
        mvc.perform(post("/api/workspaces/"+a+"/imports").header("Idempotency-Key","unknown").contentType(MediaType.APPLICATION_JSON).content(body.replace("\"Evidence\"","\"Evidence\",\"extra\":true"))).andExpect(status().isBadRequest());
        for (String invalidRevision:List.of("0.5","\"0\"","true","9223372036854775808"))
            mvc.perform(post("/api/workspaces/"+a+"/imports").header("Idempotency-Key","bad-revision").contentType(MediaType.APPLICATION_JSON).content(body.replace("\"expected_revision\":0","\"expected_revision\":"+invalidRevision))).andExpect(status().isBadRequest());
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM bulk_imports WHERE workspace_id=?",Integer.class,a));
    }
}
