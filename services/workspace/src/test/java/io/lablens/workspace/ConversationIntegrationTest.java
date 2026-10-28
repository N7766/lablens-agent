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

/** Real MySQL transactions and Redis coordination; dispatcher disabled, no provider calls. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.rate-limit=1000","lablens.cache-ttl-seconds=2","lablens.cache-namespace=java-integration"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class ConversationIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;
    private final List<String> workspaces=new ArrayList<>();
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("conversation-test-"+UUID.randomUUID())).get("id");
        workspaces.add(id); store.putDocument(id,"source",new Requests.Document("Source","SFT accuracy is 95.02 percent.","test://source")); return id;
    }
    private String conversation(String workspace) {
        return (String)store.createConversation(workspace,new Requests.Conversation("Test",null,null,null)).get("id");
    }
    private Requests.Turn turn(long revision) { return new Requests.Turn("What is its accuracy?","bm25","agent",3,1L,revision,null); }
    private void complete(String id,String answer,String resultStatus) {
        db.update("UPDATE tasks SET status='SUCCEEDED',result_json=?,finished_at=UTC_TIMESTAMP(6) WHERE id=?",
            store.encode(Map.of("answer",answer,"status",resultStatus,"agent",Map.of("state","clarification_required".equals(resultStatus)?"awaiting_clarification":"completed"))),id);
    }
    private Map<String,Object> snapshot(String id) { return store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,id)); }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM conversations WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }
    @Test void concurrentSameKeyCreatesOneTurnAndReplaysBeforeBusyAndStaleChecks() throws Exception {
        String w=workspace(),c=conversation(w);
        try (var pool=Executors.newFixedThreadPool(8)) {
            var gate=new CountDownLatch(1); var futures=new ArrayList<Future<WorkspaceStore.Submission>>();
            for (int i=0;i<8;i++) futures.add(pool.submit(()->{gate.await(); return store.submitTurn(w,c,"same",turn(0));}));
            gate.countDown(); Set<Object> ids=new HashSet<>(); int created=0;
            for (var future:futures) { var submission=future.get(15,TimeUnit.SECONDS); ids.add(submission.task().get("id")); if (!submission.reused()) created++; }
            assertEquals(1,ids.size()); assertEquals(1,created);
            assertEquals(1L,store.conversation(w,c).get("revision"));
            store.putDocument(w,"source",new Requests.Document("Changed","Changed source","test://source"));
            assertTrue(store.submitTurn(w,c,"same",turn(0)).reused());
            assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE conversation_id=?",Integer.class,c));
        }
    }
    @Test void competingNewKeysCannotAppendTwoTurnsAtSameRevision() throws Exception {
        String w=workspace(),c=conversation(w);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1); var futures=new ArrayList<Future<String>>();
            for (int i=0;i<2;i++) { String key="key-"+i; futures.add(pool.submit(()->{
                gate.await(); try { store.submitTurn(w,c,key,turn(0)); return "created"; } catch (ApiError e) { return e.code; }
            })); }
            gate.countDown(); Set<String> outcomes=new HashSet<>();
            for (var future:futures) outcomes.add(future.get(15,TimeUnit.SECONDS));
            assertEquals(Set.of("created","stale_conversation"),outcomes);
            assertEquals("conversation_busy",assertThrows(ApiError.class,()->store.submitTurn(w,c,"third",turn(1))).code);
            assertEquals(1L,store.conversation(w,c).get("revision"));
        }
    }
    @Test void consentAndQueueFailuresDoNotMutatePinnedStateOrConversationRevision() {
        String w=workspace(),c=conversation(w);
        Requests.State changed=new Requests.State("Changed goal",List.of("Use current sources"),"SFT");
        var missing=new Requests.Turn("q","bm25","agent",3,null,0L,changed);
        assertEquals("approval_required",assertThrows(ApiError.class,()->store.submitTurn(w,c,"missing",missing)).code);
        var stale=new Requests.Turn("q","bm25","agent",3,0L,0L,changed);
        assertEquals("stale_approval",assertThrows(ApiError.class,()->store.submitTurn(w,c,"stale",stale)).code);
        assertEquals(0L,store.conversation(w,c).get("revision"));
        assertEquals(Map.of("goal","","focus","","constraints",List.of()),store.conversation(w,c).get("state"));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE conversation_id=?",Integer.class,c));
    }
    @Test void nextTurnContainsCorrectedPinnedStateAndUnchangedPriorSnapshot() {
        String w=workspace(),c=conversation(w);
        var first=new Requests.Turn("SFT baseline?",null,null,3,1L,0L,new Requests.State("Compare evidence",List.of("No causal guesses"),"SFT"));
        String one=(String)store.submitTurn(w,c,"one",first).task().get("id");
        String original=db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,one);
        complete(one,"Earlier assistant draft; requires source verification.","answered");
        var correction=new Requests.Turn("Now compare DPO",null,null,3,1L,1L,new Requests.State(null,null,"DPO"));
        String two=(String)store.submitTurn(w,c,"two",correction).task().get("id");
        Map<?,?> context=(Map<?,?>)snapshot(two).get("conversation_context");
        assertEquals(c,context.get("id")); assertEquals(2,context.get("revision"));
        assertEquals(Map.of("goal","Compare evidence","constraints",List.of("No causal guesses"),"focus","DPO"),context.get("state"));
        List<?> history=(List<?>)context.get("turns"); assertEquals(1,history.size());
        Map<?,?> previous=(Map<?,?>)history.getFirst();
        assertEquals("SFT baseline?",previous.get("question")); assertEquals("SUCCEEDED",previous.get("status"));
        assertEquals(Set.of("seq","question","answer","status","snapshot_id"),previous.keySet());
        assertEquals(original,db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,one));
    }
    @Test void taskCompletionAndClarificationAreVisibleInReloadedConversationWithoutSummaryUpdates() {
        String w=workspace(),c=conversation(w);
        String id=(String)store.submitTurn(w,c,"one",turn(0)).task().get("id");
        complete(id,"Which run do you mean?","clarification_required");
        Map<?,?> saved=(Map<?,?>)((List<?>)store.conversation(w,c).get("turns")).getFirst();
        assertEquals("SUCCEEDED",saved.get("status")); assertEquals("clarification_required",saved.get("result_status"));
        assertEquals("awaiting_clarification",saved.get("agent_state")); assertEquals("Which run do you mean?",saved.get("answer"));
        assertFalse(store.submitTurn(w,c,"answer",turn(1)).reused());
    }
    @Test void failedTurnsAreTerminalAndNeverPromotedToFacts() {
        String w=workspace(),c=conversation(w); String one=(String)store.submitTurn(w,c,"one",turn(0)).task().get("id");
        db.update("UPDATE tasks SET status='FAILED',error_code='context_budget_exceeded',finished_at=UTC_TIMESTAMP(6) WHERE id=?",one);
        String two=(String)store.submitTurn(w,c,"two",turn(1)).task().get("id");
        Map<?,?> prior=(Map<?,?>)((List<?>)((Map<?,?>)snapshot(two).get("conversation_context")).get("turns")).getFirst();
        assertEquals("FAILED",prior.get("status")); assertEquals("",prior.get("answer"));
    }
    @Test void boundedHistoryLoadsLatestTwentyAndClipsAnswersWithoutChangingStoredReport() {
        String w=workspace(),c=conversation(w); String first=null;
        for (int i=0;i<22;i++) {
            String id=(String)store.submitTurn(w,c,"turn-"+i,turn(i)).task().get("id");
            if (i==0) first=id;
            complete(id,"x".repeat(4500),"answered");
        }
        String latest=(String)store.submitTurn(w,c,"latest",turn(22)).task().get("id");
        Map<?,?> context=(Map<?,?>)snapshot(latest).get("conversation_context"); List<?> history=(List<?>)context.get("turns");
        assertEquals(20,history.size()); assertEquals(2,context.get("omitted_turns"));
        assertEquals(3,((Map<?,?>)history.getFirst()).get("seq")); assertEquals(22,((Map<?,?>)history.getLast()).get("seq"));
        assertEquals(4000,((String)((Map<?,?>)history.getFirst()).get("answer")).length());
        assertEquals(4500,((String)((Map<?,?>)store.task(w,first).get("result")).get("answer")).length());
    }
    @Test void crossWorkspaceAndConversationIdempotencyCannotLeakOrReuseTurns() {
        String a=workspace(),b=workspace(),ca=conversation(a),cb=conversation(a);
        String id=(String)store.submitTurn(a,ca,"same",turn(0)).task().get("id");
        assertEquals(404,assertThrows(ApiError.class,()->store.conversation(b,ca)).status.value());
        assertEquals(404,assertThrows(ApiError.class,()->store.submitTurn(b,ca,"same",turn(0))).status.value());
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->store.submitTurn(a,cb,"same",turn(0))).code);
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->store.submit(a,"same",new Requests.Task("What is its accuracy?","bm25","agent",3,1L))).code);
        assertEquals(ca,store.task(a,id).get("conversation_id"));
        assertTrue(((List<?>)store.conversation(a,cb).get("turns")).isEmpty());
    }
    @Test void independentTasksStillOmitConversationContext() {
        String w=workspace(); String id=(String)store.submit(w,"single",new Requests.Task("q","bm25","extractive",3)).task().get("id");
        assertFalse(snapshot(id).containsKey("conversation_context")); assertNull(store.task(w,id).get("conversation_id"));
    }
    @Test void httpContractValidatesStateAndRevisionAndExposesDurableHistory() throws Exception {
        String w=workspace();
        var response=mvc.perform(post("/api/workspaces/"+w+"/conversations").contentType(MediaType.APPLICATION_JSON).content("{}"))
            .andExpect(status().isCreated()).andExpect(jsonPath("revision").value(0)).andExpect(jsonPath("state.constraints").isArray()).andReturn();
        String c=(String)store.decode(response.getResponse().getContentAsString()).get("id");
        String path="/api/workspaces/"+w+"/conversations/"+c;
        mvc.perform(get("/api/workspaces/"+w+"/conversations")).andExpect(status().isOk()).andExpect(jsonPath("conversations[0].id").value(c));
        mvc.perform(post(path+"/turns").header("Idempotency-Key","bad").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"approved_revision\":1}"))
            .andExpect(status().isBadRequest());
        mvc.perform(post(path+"/turns").header("Idempotency-Key","one").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"approved_revision\":1,\"expected_conversation_revision\":0}"))
            .andExpect(status().isAccepted()).andExpect(jsonPath("conversation_revision").value(1));
        mvc.perform(get(path)).andExpect(status().isOk()).andExpect(jsonPath("turns[0].question").value("q")).andExpect(jsonPath("turns[0].status").value("QUEUED"));
    }
}
