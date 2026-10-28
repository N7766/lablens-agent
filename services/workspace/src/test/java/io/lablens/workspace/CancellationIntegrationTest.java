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
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.rate-limit=1000","lablens.cache-ttl-seconds=2","lablens.cache-namespace=java-integration"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class CancellationIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;
    private final List<String> workspaces=new ArrayList<>();
    private String workspace() {
        String w=(String)store.createWorkspace(new Requests.Workspace("cancel-test-"+UUID.randomUUID())).get("id");
        workspaces.add(w); store.putDocument(w,"source",new Requests.Document("Source","Validation accuracy is 80 percent.","test://source")); return w;
    }
    private Requests.Task request() { return new Requests.Task("q","bm25","agent",3,1L); }
    private Requests.Turn turn(long revision) { return new Requests.Turn("q","bm25","agent",3,1L,revision,null); }
    private WorkspaceStore.Claimed running(String w,String id) {
        db.update("UPDATE tasks SET status='RUNNING',owner='owner',started_at=UTC_TIMESTAMP(6),lease_until=TIMESTAMPADD(SECOND,30,UTC_TIMESTAMP(6)) WHERE id=?",id);
        return new WorkspaceStore.Claimed(id,w,1,"hash","agent","{}");
    }
    @AfterEach void cleanup() {
        for (String w:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",w); db.update("DELETE FROM conversations WHERE workspace_id=?",w);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",w); db.update("DELETE FROM documents WHERE workspace_id=?",w);
            db.update("DELETE FROM workspaces WHERE id=?",w);
        }
    }
    @Test void queuedCancellationIsZeroCostDurableIdempotentAndCannotBeClaimed() {
        String w=workspace(),id=(String)store.submit(w,"key",request()).task().get("id");
        Map<String,Object> cancelled=store.cancel(w,id);
        assertEquals("CANCELLED",cancelled.get("status")); assertNotNull(cancelled.get("cancel_acknowledged_at"));
        assertEquals(WorkerResults.zeroUsage(false),cancelled.get("execution_usage")); assertNull(store.claim("worker",30));
        assertEquals(cancelled,store.cancel(w,id));
        assertEquals("CANCELLED",store.submit(w,"key",request()).task().get("status"));
    }
    @Test void runningCancellationKeepsConversationBusyUntilAckEvenAfterLeaseExpiry() {
        String w=workspace(),c=(String)store.createConversation(w,new Requests.Conversation("Test",null,null,null)).get("id");
        String id=(String)store.submitTurn(w,c,"one",turn(0)).task().get("id"); var claim=running(w,id);
        Map<String,Object> pending=store.cancel(w,id);
        assertEquals("CANCEL_REQUESTED",pending.get("status")); assertEquals(true,pending.get("usage_unknown")); assertNull(pending.get("finished_at"));
        assertEquals("conversation_busy",assertThrows(ApiError.class,()->store.submitTurn(w,c,"blocked",turn(1))).code);
        db.update("UPDATE tasks SET lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",id);
        store.expireLeases(); assertEquals("CANCEL_REQUESTED",store.task(w,id).get("status"));
        assertFalse(store.finish(claim,"owner","{}",store.encode(WorkerResults.zeroUsage(false)),false,5));
        assertFalse(store.fail(claim,"owner","error","late error",false,5));
        assertTrue(store.acknowledgeCancellation(id,null));
        assertEquals(true,store.task(w,id).get("usage_unknown"));
        assertFalse(store.acknowledgeCancellation(id,WorkerResults.zeroUsage(false)));
        String next=(String)store.submitTurn(w,c,"next",turn(1)).task().get("id");
        Map<?,?> context=(Map<?,?>)store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,next)).get("conversation_context");
        Map<?,?> old=(Map<?,?>)((List<?>)context.get("turns")).getFirst();
        assertEquals("CANCELLED",old.get("status")); assertEquals("",old.get("answer"));
    }
    @Test void knownCancellationUsageAndSafeTraceSurviveAcknowledgementAndLateCompletionIsBlocked() {
        String w=workspace(),id=(String)store.submit(w,"key",request()).task().get("id"); var claim=running(w,id);
        store.cancel(w,id); var usage=WorkerResults.zeroUsage(false); usage.put("model_calls",1);
        var trace=Map.<String,Object>of("status","agent_cancelled","agent",Map.of("state","cancelled"));
        assertTrue(store.recordCancellationResult(claim,"owner",usage,trace,10));
        assertTrue(store.acknowledgeCancellation(id,usage));
        assertEquals(usage,store.task(w,id).get("execution_usage")); assertEquals(trace,store.task(w,id).get("error_result"));
        assertFalse(store.finish(claim,"owner","{\"answer\":\"late\"}",store.encode(usage),false,10));
        assertNull(store.task(w,id).get("result"));
    }
    @Test void cancellationCannotCrossWorkspaceAndTerminalResultsAreNotRewritten() {
        String a=workspace(),b=workspace(),id=(String)store.submit(a,"key",request()).task().get("id");
        assertEquals(404,assertThrows(ApiError.class,()->store.cancel(b,id)).status.value());
        var claim=running(a,id);
        assertTrue(store.finish(claim,"owner","{\"answer\":\"done\"}",store.encode(WorkerResults.zeroUsage(false)),false,5));
        Map<String,Object> before=store.task(a,id); assertEquals(before,store.cancel(a,id));
    }
    @Test void claimRacingCancelNeverLeavesClaimableOrSuccessfulCancelledTask() throws Exception {
        String w=workspace(),id=(String)store.submit(w,"key",request()).task().get("id");
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1);
            var claimant=pool.submit(()->{gate.await(); return store.claim("owner",30);});
            var cancellation=pool.submit(()->{gate.await(); return store.cancel(w,id);});
            gate.countDown(); var claim=claimant.get(3,TimeUnit.SECONDS); cancellation.get(3,TimeUnit.SECONDS);
            String status=(String)store.task(w,id).get("status");
            assertTrue(Set.of("CANCELLED","CANCEL_REQUESTED").contains(status));
            if (claim!=null) assertFalse(store.finish(claim,"owner","{}",store.encode(WorkerResults.zeroUsage(false)),false,5));
            assertNull(store.claim("other",30));
        }
    }
    @Test void httpCancellationAcknowledgesQueuedAndReturnsAcceptedForRunning() throws Exception {
        String w=workspace(),queued=(String)store.submit(w,"queued",request()).task().get("id");
        mvc.perform(post("/api/workspaces/"+w+"/tasks/"+queued+"/cancel")).andExpect(status().isOk()).andExpect(jsonPath("status").value("CANCELLED"));
        String running=(String)store.submit(w,"running",request()).task().get("id"); running(w,running);
        mvc.perform(post("/api/workspaces/"+w+"/tasks/"+running+"/cancel")).andExpect(status().isAccepted()).andExpect(jsonPath("status").value("CANCEL_REQUESTED"));
        mvc.perform(post("/api/workspaces/"+w+"/tasks/"+running+"/cancel")).andExpect(status().isAccepted()).andExpect(jsonPath("status").value("CANCEL_REQUESTED"));
    }
}
