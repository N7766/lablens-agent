package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;

/** Opt-in tests require genuine MySQL + Redis; no H2 substitution or embedded fake services. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.rate-limit=1000","lablens.cache-ttl-seconds=2","lablens.cache-namespace=java-integration"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class WorkspaceIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired RedisSupport redis;
    @Autowired MockMvc mvc;
    @Autowired TransactionTemplate tx;
    private final List<String> workspaces=new ArrayList<>();
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("java-test-"+UUID.randomUUID())).get("id");
        workspaces.add(id); store.putDocument(id,"evidence",new Requests.Document("Evidence","Accuracy is 86 percent.","test://evidence")); return id;
    }
    private Requests.Task request() { return new Requests.Task("What is the accuracy?","bm25","extractive",3); }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }
    @Test void simultaneousIdenticalSubmissionsCreateExactlyOneDurableTask() throws Exception {
        String workspace=workspace();
        try (var pool=Executors.newFixedThreadPool(12)) {
            var gate=new CountDownLatch(1);
            var futures=new ArrayList<Future<WorkspaceStore.Submission>>();
            for (int i=0;i<12;i++) futures.add(pool.submit(()->{gate.await(); return store.submit(workspace,"same-key",request());}));
            gate.countDown(); Set<Object> ids=new HashSet<>(); int created=0;
            for (var future:futures) { var result=future.get(15,TimeUnit.SECONDS); ids.add(result.task().get("id")); if (!result.reused()) created++; }
            assertEquals(1,ids.size()); assertEquals(1,created);
            assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,workspace));
        }
    }
    @Test void conflictingIdempotencyKeyIsRejectedAndDefaultNormalizationReuses() {
        String workspace=workspace(); var first=store.submit(workspace,"key",new Requests.Task("q",null,null,null,1L));
        assertEquals(first.task().get("id"),store.submit(workspace,"key",new Requests.Task(" q ","hybrid","extractive",5,1L)).task().get("id"));
        ApiError error=assertThrows(ApiError.class,()->store.submit(workspace,"key",request()));
        assertEquals(409,error.status.value());
    }
    @Test void snapshotsRemainImmutableAfterDocumentUpdateAndDeletion() {
        String workspace=workspace(); String task=(String)store.submit(workspace,"before",request()).task().get("id");
        String original=db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,task);
        store.putDocument(workspace,"evidence",new Requests.Document("New","Accuracy is 91 percent.","test://new"));
        store.deleteDocument(workspace,"evidence");
        assertEquals(original,db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,task));
        assertTrue(original.contains("86 percent")); assertFalse(original.contains("91 percent"));
        assertEquals(3L,((Number)store.documents(workspace).get("revision")).longValue());
        assertEquals(3,db.queryForObject("SELECT COUNT(*) FROM document_versions WHERE workspace_id=?",Integer.class,workspace));
    }
    @Test void sameKeyAfterRevisionChangeStillReturnsOriginalTask() {
        String workspace=workspace(); var first=store.submit(workspace,"key",request());
        store.putDocument(workspace,"extra",new Requests.Document("Other","Second document.",""));
        var repeated=store.submit(workspace,"key",request());
        assertTrue(repeated.reused()); assertEquals(first.task().get("id"),repeated.task().get("id"));
        assertEquals(1L,repeated.task().get("snapshot_revision"));
    }
    @Test void workspaceAndTaskBoundariesAreEnforced() {
        String a=workspace(),b=workspace(); String task=(String)store.submit(a,"key",request()).task().get("id");
        assertThrows(ApiError.class,()->store.task(b,task));
        store.putDocument(a,"private",new Requests.Document("Private","Only A",""));
        assertThrows(ApiError.class,()->store.document(b,"private"));
    }
    @Test void snapshotCapacityRejectedWithoutPartialDocumentOrRevisionMutation() {
        String workspace=workspace();
        store.deleteDocument(workspace,"evidence");
        for (int i=0;i<10;i++) store.putDocument(workspace,"large-"+i,new Requests.Document("Large","x".repeat(20000),""));
        long revision=((Number)store.documents(workspace).get("revision")).longValue();
        assertEquals(413,assertThrows(ApiError.class,()->store.putDocument(workspace,"extra",new Requests.Document("Extra","x",""))).status.value());
        assertEquals(revision,store.documents(workspace).get("revision"));
        assertEquals(10,db.queryForObject("SELECT COUNT(*) FROM documents WHERE workspace_id=?",Integer.class,workspace));
    }
    @Test void expiredLiveLeaseFailsExplicitlyAndStaleOwnerCannotCommit() {
        String workspace=workspace(); String id=(String)store.submit(workspace,"live",new Requests.Task("q","bm25","live",1,1L)).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='dead-worker',lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",id);
        assertTrue(store.expireLeases()>=1);
        Map<String,Object> task=store.task(workspace,id);
        assertEquals("FAILED",task.get("status")); assertEquals(true,task.get("usage_unknown"));
        assertEquals("worker_interrupted",task.get("error_code"));
        assertFalse(store.finish(new WorkspaceStore.Claimed(id,workspace,1,"h","live","{}"),"dead-worker","{}",store.encode(WorkerResults.zeroUsage(false)),false,10));
        assertTrue(store.submit(workspace,"live",new Requests.Task("q","bm25","live",1,1L)).reused());
    }
    @Test void completionRequiresCurrentOwnerAndUnexpiredLease() {
        String workspace=workspace(); String id=(String)store.submit(workspace,"key",request()).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='owner-a',lease_until=TIMESTAMPADD(SECOND,30,UTC_TIMESTAMP(6)) WHERE id=?",id);
        var claimed=new WorkspaceStore.Claimed(id,workspace,1,"h","extractive","{}");
        assertFalse(store.finish(claimed,"owner-b","{}",store.encode(WorkerResults.zeroUsage(false)),false,5));
        assertTrue(store.finish(claimed,"owner-a","{\"answer\":\"ok\"}",store.encode(WorkerResults.zeroUsage(false)),false,5));
        assertFalse(store.finish(claimed,"owner-a","{\"answer\":\"duplicate\"}",store.encode(WorkerResults.zeroUsage(false)),false,5));
        assertEquals(Map.of("answer","ok"),store.task(workspace,id).get("result"));
    }
    @Test void expiredDenseExtractiveLeaseAlsoMarksUsageUnknown() {
        String workspace=workspace(); String id=(String)store.submit(workspace,"dense",new Requests.Task("q","dense","extractive",1,1L)).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='dead-worker',lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",id);
        store.expireLeases();
        Map<String,Object> task=store.task(workspace,id);
        assertEquals("FAILED",task.get("status")); assertEquals(true,task.get("usage_unknown"));
        Map<?,?> usage=(Map<?,?>)task.get("execution_usage");
        assertEquals(true,usage.get("provider_usage_unknown")); assertNull(usage.get("embedding_requests"));
    }
    @Test void expiredAgentBm25LeaseMarksGenerationUsageUnknown() {
        String workspace=workspace(); String id=(String)store.submit(workspace,"agent",new Requests.Task("q","bm25","agent",1,1L)).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='dead-worker',lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",id);
        store.expireLeases();
        Map<String,Object> task=store.task(workspace,id);
        assertEquals("FAILED",task.get("status")); assertEquals(true,task.get("usage_unknown"));
        assertEquals(true,((Map<?,?>)task.get("execution_usage")).get("provider_usage_unknown"));
    }
    @Test void expiryDoesNotWaitForAnUnexpiredTaskLockedByCompletion() throws Exception {
        String workspace=workspace();
        String active=(String)store.submit(workspace,"active",request()).task().get("id");
        String expired=(String)store.submit(workspace,"expired",request()).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='owner',lease_until=TIMESTAMPADD(SECOND,60,UTC_TIMESTAMP(6)) WHERE id=?",active);
        db.update("UPDATE tasks SET status='RUNNING',owner='owner',lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",expired);
        var locked=new CountDownLatch(1); var release=new CountDownLatch(1);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var holder=pool.submit(()->tx.executeWithoutResult(status->{
                db.queryForObject("SELECT id FROM tasks WHERE id=? FOR UPDATE",String.class,active);
                locked.countDown();
                try { release.await(5,TimeUnit.SECONDS); }
                catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
            }));
            try {
                assertTrue(locked.await(3,TimeUnit.SECONDS));
                var sweep=pool.submit(store::expireLeases);
                assertTrue(sweep.get(2,TimeUnit.SECONDS)>=1,"Expiry must not acquire the unrelated unexpired row lock");
                assertEquals("FAILED",store.task(workspace,expired).get("status"));
                assertEquals("RUNNING",store.task(workspace,active).get("status"));
            } finally { release.countDown(); holder.get(3,TimeUnit.SECONDS); }
        }
    }
    @Test void safeFailureUsageAndCachedOriginArePersistedSeparately() {
        String workspace=workspace(); String id=(String)store.submit(workspace,"key",request()).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='owner-a',lease_until=TIMESTAMPADD(SECOND,30,UTC_TIMESTAMP(6)) WHERE id=?",id);
        var claimed=new WorkspaceStore.Claimed(id,workspace,1,"h","extractive","{}");
        var usage=WorkerResults.zeroUsage(false); usage.put("embedding_requests",2);
        var error=Map.<String,Object>of("status","provider_error","usage",usage,"error","Safe provider failure");
        assertTrue(store.fail(claimed,"owner-a","provider_failed","safe",usage,error,5));
        assertEquals(usage,store.task(workspace,id).get("execution_usage"));
        assertEquals(error,store.task(workspace,id).get("error_result"));
    }
    @Test void omittedAndLegacyBlankSourceGetDeterministicLabelsInSnapshots() {
        String workspace=workspace();
        assertEquals("document:omitted",store.putDocument(workspace,"omitted",new Requests.Document("Title","Body",null)).get("source"));
        db.update("UPDATE documents SET source='' WHERE workspace_id=? AND id='evidence'",workspace);
        String task=(String)store.submit(workspace,"source",request()).task().get("id");
        String snapshot=db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,task);
        assertTrue(snapshot.contains("document:evidence")); assertTrue(snapshot.contains("document:omitted"));
    }
    @Test void redisCacheSeparatesRevisionAndWorkspaceAndExpires() throws Exception {
        String a=workspace(),b=workspace();
        var first=new WorkspaceStore.Claimed("t",a,1,"hash","extractive","{}");
        redis.put(first,"{\"answer\":\"cached\"}");
        assertNotNull(redis.get(first));
        assertNull(redis.get(new WorkspaceStore.Claimed("t",a,2,"hash","extractive","{}")));
        assertNull(redis.get(new WorkspaceStore.Claimed("t",b,1,"hash","extractive","{}")));
        Thread.sleep(2100); assertNull(redis.get(first));
    }
    @Test void everyProviderRouteRequiresCurrentRevisionAndDoesNotLeakApprovalIntoWorkerPayload() throws Exception {
        String workspace=workspace();
        for (String[] route:new String[][]{{"bm25","live"},{"bm25","agent"},{"dense","extractive"},{"hybrid","extractive"}}) {
            String key=route[0]+route[1];
            var missing=new Requests.Task("q",route[0],route[1],1);
            assertEquals("approval_required",assertThrows(ApiError.class,()->store.submit(workspace,key,missing)).code);
            var stale=new Requests.Task("q",route[0],route[1],1,0L);
            assertEquals("stale_approval",assertThrows(ApiError.class,()->store.submit(workspace,key,stale)).code);
            var approved=new Requests.Task("q",route[0],route[1],1,1L);
            var task=store.submit(workspace,key,approved).task();
            assertEquals(1L,((Number)((Map<?,?>)task.get("request")).get("approved_revision")).longValue());
            assertFalse(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,task.get("id")).contains("approved_revision"));
        }
        mvc.perform(post("/api/workspaces/"+workspace+"/tasks").header("Idempotency-Key","missing-http")
            .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"mode\":\"agent\",\"strategy\":\"bm25\"}"))
            .andExpect(status().isConflict()).andExpect(jsonPath("error").value("approval_required"));
        assertEquals(4,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,workspace));
    }
    @Test void approvedIdempotencyReplaySurvivesMutationButNewTaskNeedsNewConsent() {
        String workspace=workspace(); var approved=new Requests.Task("q","bm25","agent",1,1L);
        var first=store.submit(workspace,"approved",approved);
        store.putDocument(workspace,"extra",new Requests.Document("Other","Changed data.","test://other"));
        assertEquals(first.task().get("id"),store.submit(workspace,"approved",approved).task().get("id"));
        assertEquals("stale_approval",assertThrows(ApiError.class,()->store.submit(workspace,"new",approved)).code);
        var renewed=new Requests.Task("q","bm25","agent",1,2L);
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->store.submit(workspace,"approved",renewed)).code);
        assertEquals(2L,store.submit(workspace,"new",renewed).task().get("snapshot_revision"));
    }
    @Test void approvalIsCheckedAfterAcquiringTheDocumentMutationLock() throws Exception {
        String workspace=workspace(); var mutated=new CountDownLatch(1); var release=new CountDownLatch(1);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var mutation=pool.submit(()->tx.executeWithoutResult(status->{
                store.putDocument(workspace,"extra",new Requests.Document("Other","Concurrent update.","test://other"));
                mutated.countDown();
                try { release.await(5,TimeUnit.SECONDS); }
                catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
            }));
            assertTrue(mutated.await(3,TimeUnit.SECONDS));
            var submission=pool.submit(()->store.submit(workspace,"concurrent",new Requests.Task("q","bm25","agent",1,1L)));
            try { assertThrows(TimeoutException.class,()->submission.get(100,TimeUnit.MILLISECONDS)); }
            finally { release.countDown(); }
            mutation.get(3,TimeUnit.SECONDS);
            var failure=assertThrows(ExecutionException.class,()->submission.get(3,TimeUnit.SECONDS));
            assertEquals("stale_approval",((ApiError)failure.getCause()).code);
            assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,workspace));
        }
    }
    @Test void httpValidationRejectsMissingKeyUnknownFieldsAndOversizedDocument() throws Exception {
        String workspace=workspace();
        mvc.perform(post("/api/workspaces/"+workspace+"/tasks").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\"}")).andExpect(status().isBadRequest());
        mvc.perform(post("/api/workspaces/"+workspace+"/tasks").header("Idempotency-Key","k").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"unsupported\":true}")).andExpect(status().isBadRequest());
        mvc.perform(put("/api/workspaces/"+workspace+"/documents/large").contentType(MediaType.APPLICATION_JSON).content(store.encode(Map.of("title","title","text","x".repeat(20001))))).andExpect(status().isBadRequest());
        mvc.perform(post("/api/workspaces/"+workspace+"/tasks").header("Idempotency-Key","k").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"strategy\":\"bm25\"}")).andExpect(status().isAccepted()).andExpect(jsonPath("reused").value(false));
        mvc.perform(post("/api/workspaces/"+workspace+"/tasks").header("Idempotency-Key","k").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"strategy\":\"bm25\"}")).andExpect(status().isOk()).andExpect(jsonPath("reused").value(true));
    }
}
