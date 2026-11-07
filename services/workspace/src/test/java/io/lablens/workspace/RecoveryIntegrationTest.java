package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.Cookie;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

@SpringBootTest(properties={"lablens.auth.enabled=true","lablens.auth.origin=http://127.0.0.1:8086",
    "lablens.auth.bootstrap-username=round18admin","lablens.auth.bootstrap-password=round18-fixture-admin-password",
    "lablens.service-key=round18-fixture-service-key-1234567890","lablens.auth.login-attempts-per-minute=1000",
    "lablens.checkpoints-enabled=true","lablens.dispatch-enabled=false","lablens.import-dispatch-enabled=false",
    "lablens.mq.enabled=true","lablens.mq.relay-enabled=false","lablens.mq.consumer-auto-startup=false",
    "lablens.max-pending-tasks=2","lablens.max-pending-global=2","lablens.rate-limit=10000"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class RecoveryIntegrationTest {
    @Autowired WorkspaceStore store;@Autowired RecoveryStore recovery;@Autowired AuthStore auth;@Autowired MemoryStore memories;
    @Autowired JdbcTemplate db;@Autowired MockMvc mvc;@Autowired ObjectMapper json;
    String suffix=UUID.randomUUID().toString().substring(0,8),name="ra-"+suffix,otherName="rb-"+suffix;
    AuthStore.Identity alice,bob;List<String> workspaces=new ArrayList<>();
    String owner=UUID.randomUUID().toString();
    @BeforeAll void users() {
        auth.createUser(name,"round18-fixture-alice-password",false);auth.createUser(otherName,"round18-fixture-bob-password",false);
        alice=auth.login(name,"round18-fixture-alice-password","127.0.0.1");bob=auth.login(otherName,"round18-fixture-bob-password","127.0.0.1");
    }
    @AfterEach void cleanup() {
        for(String w:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",w);db.update("DELETE FROM conversations WHERE workspace_id=?",w);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",w);db.update("DELETE FROM documents WHERE workspace_id=?",w);db.update("DELETE FROM workspaces WHERE id=?",w);
        }workspaces.clear();
    }
    @AfterAll void usersCleanup() {auth.logout(alice);auth.logout(bob);db.update("DELETE FROM auth_users WHERE username IN (?,?)",name,otherName);}
    String workspace() {
        String w=(String)auth.createWorkspace(new Requests.Workspace("Recovery fixture"),alice,store).get("id");workspaces.add(w);
        store.putDocument(w,"source",new Requests.Document("Source","Exact original source.","fixture://source"));return w;
    }
    String task(String w) {return (String)store.submit(w,UUID.randomUUID().toString(),new Requests.Task("q","bm25","agent",3,1L)).task().get("id");}
    void running(String id) {db.update("UPDATE tasks SET status='RUNNING',owner=?,started_at=UTC_TIMESTAMP(6),lease_until=UTC_TIMESTAMP(6)+INTERVAL 90 SECOND WHERE id=?",owner,id);}
    void failed(String id) {db.update("UPDATE tasks SET status='FAILED',finished_at=UTC_TIMESTAMP(6),lease_until=NULL WHERE id=?",id);}
    RecoveryStore.Checkpoint boundary(int rev) {
        String payload="{\"elapsed_ms\":120,\"next_step\":2,\"value\":\"中文 😀\"}";
        return new RecoveryStore.Checkpoint(rev,"boundary",null,Map.of("payload_json",payload),Requests.sha256(payload));
    }
    RecoveryStore.Checkpoint operation(int rev,String id) {return new RecoveryStore.Checkpoint(rev,"operation_started",id,null,null);}
    RecoveryStore.Resume approval(String w,int rev) {return new RecoveryStore.Resume(w+":1",rev);}
    MockHttpServletRequestBuilder as(MockHttpServletRequestBuilder r,AuthStore.Identity who) {return r.cookie(new Cookie(AuthFilter.COOKIE,who.token())).header("Origin","http://127.0.0.1:8086").header("X-CSRF-Token",who.csrf());}
    MockHttpServletRequestBuilder callback(String id) {return post("/api/internal/tasks/"+id+"/checkpoint").header("X-LabLens-Service-Key","round18-fixture-service-key-1234567890").header("X-LabLens-Execution-Owner",owner).contentType(MediaType.APPLICATION_JSON);}
    @Test void appendOnlyReceiptsAreIdempotentAndRequireMonotonicSequence() {
        String w=workspace(),id=task(w);running(id);
        assertEquals(false,recovery.checkpoint(id,owner,boundary(1)).get("reused"));assertEquals(true,recovery.checkpoint(id,owner,boundary(1)).get("reused"));
        assertEquals("checkpoint_revision_conflict",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,boundary(3))).code);
        assertEquals("checkpoint_revision_conflict",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,operation(1,"decision:1"))).code);
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM task_checkpoints WHERE task_id=?",Integer.class,id));
    }
    @Test void multipleOperationsInOneBatchRemainUnknownUntilCompletedBoundary() {
        String w=workspace(),id=task(w);running(id);
        assertEquals("checkpoint_pending",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,operation(1,"decision:1"))).code);
        recovery.checkpoint(id,owner,boundary(1));recovery.checkpoint(id,owner,operation(2,"decision:1"));recovery.checkpoint(id,owner,operation(3,"review:1"));
        failed(id);assertEquals("unknown_outcome",recovery.status(w,id).get("status"));
        assertEquals("recovery_unavailable",assertThrows(ApiError.class,()->recovery.resume(w,id,"resume",approval(w,3),alice)).code);
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,w));
    }
    @Test void completedBatchPreservesOriginalSnapshotAndCreatesOneChildAndOutbox() {
        String w=workspace(),id=task(w);running(id);recovery.checkpoint(id,owner,boundary(1));recovery.checkpoint(id,owner,operation(2,"decision:1"));recovery.checkpoint(id,owner,boundary(3));failed(id);
        var parent=store.task(w,id);String original=db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,id);
        store.putDocument(w,"source",new Requests.Document("Changed","new body","fixture://new"));
        var result=recovery.resume(w,id,"resume",approval(w,3),alice);String child=(String)result.task().get("id");
        assertFalse(result.reused());assertEquals(id,result.task().get("parent_task_id"));assertNull(result.task().get("conversation_id"));assertEquals(parent,store.task(w,id));
        var resumed=store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,child));
        assertNotNull(resumed.remove("resume_context"));assertEquals(store.decode(original),resumed);
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE task_id=?",Integer.class,child));
        assertTrue(recovery.resume(w,id,"resume",approval(w,3),alice).reused());assertEquals(child,recovery.resume(w,id,"different-key",approval(w,3),alice).task().get("id"));
        assertEquals("already_resumed",recovery.status(w,id).get("status"));
        running(child);recovery.checkpoint(child,owner,boundary(1));failed(child);assertEquals("unavailable",recovery.status(w,child).get("status"));
    }
    @Test void ownerLeaseTerminalAndCancellationFenceEveryCallbackEvenReplays() {
        String w=workspace(),id=task(w);running(id);recovery.checkpoint(id,owner,boundary(1));
        assertEquals("checkpoint_fenced",assertThrows(ApiError.class,()->recovery.checkpoint(id,UUID.randomUUID().toString(),boundary(1))).code);
        db.update("UPDATE tasks SET lease_until=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE id=?",id);
        assertEquals("checkpoint_fenced",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,boundary(1))).code);
        running(id);db.update("UPDATE tasks SET status='CANCEL_REQUESTED' WHERE id=?",id);
        assertEquals("active",recovery.status(w,id).get("status"));assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,boundary(2)));
        failed(id);assertEquals("checkpoint_fenced",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,operation(2,"late"))).code);
    }
    @Test void noCheckpointActiveAndWrongApprovalsDoNotCreateContinuations() {
        String w=workspace(),id=task(w);assertEquals("active",recovery.status(w,id).get("status"));failed(id);assertEquals("no_checkpoint",recovery.status(w,id).get("status"));
        running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);
        for (RecoveryStore.Resume bad:List.of(new RecoveryStore.Resume(w+":2",1),approval(w,2)))
            assertEquals("stale_recovery_approval",assertThrows(ApiError.class,()->recovery.resume(w,id,UUID.randomUUID().toString(),bad,alice)).code);
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,w));
    }
    @Test void rejectsHashMismatchOversizeAndMalformedStateWithoutReceipt() {
        String w=workspace(),id=task(w);running(id);
        assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,new RecoveryStore.Checkpoint(1,"boundary",null,Map.of("payload_json","{}"),"0".repeat(64))));
        String huge="{\"body\":\""+"x".repeat(RecoveryStore.MAX_BYTES)+"\"}";
        assertEquals("checkpoint_too_large",assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,new RecoveryStore.Checkpoint(1,"boundary",null,Map.of("payload_json",huge),Requests.sha256(huge)))).code);
        assertThrows(ApiError.class,()->recovery.checkpoint(id,owner,new RecoveryStore.Checkpoint(1,"boundary",null,Map.of("payload_json","[]"),Requests.sha256("[]"))));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_checkpoints WHERE task_id=?",Integer.class,id));
    }
    @Test void callbackAuthenticationDoesNotAcceptBrowserSessionsOrExposeServiceCredentials() throws Exception {
        String w=workspace(),id=task(w);running(id);String body=json.writeValueAsString(boundary(1));
        mvc.perform(post("/api/internal/tasks/"+id+"/checkpoint").contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isUnauthorized());
        mvc.perform(as(callback(id),alice).content(body)).andExpect(status().isUnauthorized());
        mvc.perform(callback(id).content(body)).andExpect(status().isOk()).andExpect(jsonPath("revision").value(1));
        mvc.perform(get("/api/workspaces/"+w+"/tasks/"+id+"/recovery").header("X-LabLens-Service-Key","round18-fixture-service-key-1234567890")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/internal/tasks/"+id+"/checkpoint").header("X-LabLens-Service-Key","round18-fixture-service-key-1234567890")).andExpect(status().isUnauthorized());
    }
    @Test void browserRecoveryUsesWorkspaceRolesCsrfAndContainsNoOpaqueState() throws Exception {
        String w=workspace(),id=task(w);running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);
        String path="/api/workspaces/"+w+"/tasks/"+id;
        mvc.perform(get(path+"/recovery")).andExpect(status().isUnauthorized());mvc.perform(as(get(path+"/recovery"),bob)).andExpect(status().isNotFound());
        auth.setMember(w,otherName,"VIEWER",alice);
        mvc.perform(as(get(path+"/recovery"),bob)).andExpect(status().isOk()).andExpect(jsonPath("status").value("resumable")).andExpect(jsonPath("state").doesNotExist()).andExpect(jsonPath("owner").doesNotExist());
        mvc.perform(as(post(path+"/resume"),bob).header("Idempotency-Key","resume").contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(approval(w,1)))).andExpect(status().isForbidden());
        mvc.perform(post(path+"/resume").cookie(new Cookie(AuthFilter.COOKIE,alice.token())).header("Origin","http://127.0.0.1:8086").header("Idempotency-Key","resume").contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(approval(w,1)))).andExpect(status().isForbidden());
    }
    @Test void selectedMemorySurvivesAuthenticatedDecisionReviewReceiptsAndExplicitContinuation() throws Exception {
        String w=workspace();var created=memories.write(w,null,new MemoryRequests.Mutation("source-hint","source",1L,"Exact original source.","hint",null,0L,null),alice);
        String memory=(String)((Map<?,?>)created.get("memory")).get("id");
        String id=(String)store.submit(w,"memory-task",new Requests.Task("q","bm25","agent",3,1L,List.of(memory),1L)).task().get("id");running(id);
        var before=store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,id));
        for(var input:List.of(boundary(1),operation(2,"decision:1"),operation(3,"review:1"),boundary(4)))
            mvc.perform(callback(id).content(json.writeValueAsString(input))).andExpect(status().isOk()).andExpect(jsonPath("revision").value(input.revision()));
        failed(id);memories.delete(w,memory,new MemoryRequests.Delete(1L,1L),alice);
        var response=mvc.perform(as(post("/api/workspaces/"+w+"/tasks/"+id+"/resume"),alice).header("Idempotency-Key","resume")
            .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(approval(w,4)))).andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString();
        String child=(String)store.decode(response).get("id");var snapshot=store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,child));
        assertEquals(before.get("memory_context"),snapshot.get("memory_context"));
        assertEquals(4,db.queryForObject("SELECT COUNT(*) FROM task_checkpoints WHERE task_id=?",Integer.class,id));
    }
    @Test void concurrentResumeUsesOneChildWithoutDuplicatingProviderAdmission() throws Exception {
        String w=workspace(),id=task(w);running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);
        try(var pool=Executors.newFixedThreadPool(2)) {
            CountDownLatch start=new CountDownLatch(1);var jobs=new ArrayList<Future<WorkspaceStore.Submission>>();
            for(int i=0;i<2;i++) jobs.add(pool.submit(()->{start.await();return recovery.resume(w,id,"same-key",approval(w,1),alice);}));
            start.countDown();var a=jobs.get(0).get(10,TimeUnit.SECONDS);var b=jobs.get(1).get(10,TimeUnit.SECONDS);
            assertEquals(a.task().get("id"),b.task().get("id"));assertNotEquals(a.reused(),b.reused());
        }
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE parent_task_id=?",Integer.class,id));
    }
    @Test void conversationRevisionIsNotAdvancedAndActiveChildBlocksNextTurn() {
        String w=workspace(),conversation=(String)store.createConversation(w,new Requests.Conversation("dialog",null,null,null)).get("id");
        String id=(String)store.submitTurn(w,conversation,"turn",new Requests.Turn("q","bm25","agent",3,1L,0L,null)).task().get("id");
        running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);
        String child=(String)recovery.resume(w,id,"resume",approval(w,1),alice).task().get("id");
        assertEquals(1L,store.conversation(w,conversation).get("revision"));assertEquals(1,((List<?>)store.conversation(w,conversation).get("turns")).size());
        assertEquals("conversation_busy",assertThrows(ApiError.class,()->store.submitTurn(w,conversation,"next",new Requests.Turn("next","bm25","agent",3,1L,1L,null))).code);
        failed(child);
        store.submitTurn(w,conversation,"next",new Requests.Turn("next","bm25","agent",3,1L,1L,null));
        assertEquals(2L,store.conversation(w,conversation).get("revision"));
    }
    @Test void newerConversationTurnMakesOldCheckpointUnavailable() {
        String w=workspace(),conversation=(String)store.createConversation(w,new Requests.Conversation("dialog",null,null,null)).get("id");
        String id=(String)store.submitTurn(w,conversation,"turn",new Requests.Turn("q","bm25","agent",3,1L,0L,null)).task().get("id");running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);
        store.submitTurn(w,conversation,"next",new Requests.Turn("next","bm25","agent",3,1L,1L,null));
        assertEquals("unavailable",recovery.status(w,id).get("status"));assertEquals("recovery_unavailable",assertThrows(ApiError.class,()->recovery.resume(w,id,"resume",approval(w,1),alice)).code);
    }
    @Test void recoverySharesGlobalCapacityAndDoesNotBypassIdempotencyConflicts() {
        String w=workspace(),id=task(w);running(id);recovery.checkpoint(id,owner,boundary(1));failed(id);task(w);task(w);
        assertEquals("global_queue_full",assertThrows(ApiError.class,()->recovery.resume(w,id,"resume",approval(w,1),alice)).code);
        db.update("UPDATE tasks SET status='FAILED' WHERE workspace_id=? AND status='QUEUED'",w);
        recovery.resume(w,id,"resume",approval(w,1),alice);
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->recovery.resume(w,id,"resume",approval(w,2),alice)).code);
    }
}
