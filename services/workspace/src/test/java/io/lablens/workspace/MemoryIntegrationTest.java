package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.Cookie;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
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

/** Real MySQL source/version transactions, authenticated ACL and existing shared task admission. */
@SpringBootTest(properties={"lablens.auth.enabled=true","lablens.auth.origin=http://127.0.0.1:8086",
    "lablens.auth.bootstrap-username=round17admin","lablens.auth.bootstrap-password=round17-fixture-admin-password",
    "lablens.service-key=round17-fixture-service-key-1234567890","lablens.auth.login-attempts-per-minute=1000",
    "lablens.dispatch-enabled=false","lablens.import-dispatch-enabled=false","lablens.mq.enabled=false",
    "lablens.max-pending-tasks=2","lablens.max-pending-global=2","lablens.rate-limit=10000"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class MemoryIntegrationTest {
    @Autowired WorkspaceStore store; @Autowired MemoryStore memories; @Autowired AuthStore auth;
    @Autowired JdbcTemplate db; @Autowired MockMvc mvc; @Autowired ObjectMapper json;
    String suffix=UUID.randomUUID().toString().substring(0,8),aliceName="ma-"+suffix,bobName="mb-"+suffix;
    AuthStore.Identity alice,bob; List<String> workspaces=new ArrayList<>();
    static final String BODY="SFT baseline 95.02 percent.\n中文 😀 exact source.";
    @BeforeAll void users() {
        auth.createUser(aliceName,"round17-fixture-alice-password",false); auth.createUser(bobName,"round17-fixture-bob-password",false);
        alice=auth.login(aliceName,"round17-fixture-alice-password","127.0.0.1"); bob=auth.login(bobName,"round17-fixture-bob-password","127.0.0.1");
    }
    @AfterEach void cleanup() {
        for (String w:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",w); db.update("DELETE FROM conversations WHERE workspace_id=?",w);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",w); db.update("DELETE FROM documents WHERE workspace_id=?",w);
            db.update("DELETE FROM workspaces WHERE id=?",w);
        }
        workspaces.clear();
    }
    @AfterAll void usersCleanup() { auth.logout(alice);auth.logout(bob); db.update("DELETE FROM auth_users WHERE username IN (?,?)",aliceName,bobName); }
    String workspace() {
        String w=(String)auth.createWorkspace(new Requests.Workspace("Memory fixture"),alice,store).get("id");workspaces.add(w);
        store.putDocument(w,"source",new Requests.Document("Source",BODY,"fixture://source"));return w;
    }
    MemoryRequests.Mutation input(String key,long revision,Long version) { return new MemoryRequests.Mutation(key,"source",1L,"SFT baseline 95.02 percent.","untrusted note",null,revision,version); }
    Map<String,Object> create(String w) { return memories.write(w,null,input("baseline",0,null),alice); }
    @SuppressWarnings("unchecked") Map<String,Object> entry(Map<String,Object> response) { return (Map<String,Object>)response.get("memory"); }
    String id(Map<String,Object> response) { return (String)entry(response).get("id"); }
    Requests.Task selected(String id,Long revision) { return new Requests.Task("Is SFT available?","bm25","agent",3,1L,List.of(id),revision); }
    Map<String,Object> snapshot(String id) { return store.decode(db.queryForObject("SELECT snapshot_json FROM tasks WHERE id=?",String.class,id)); }
    MockHttpServletRequestBuilder as(MockHttpServletRequestBuilder r,AuthStore.Identity who) {
        return r.cookie(new Cookie(AuthFilter.COOKIE,who.token())).header("Origin","http://127.0.0.1:8086").header("X-CSRF-Token",who.csrf());
    }
    @Test void exactSourceBytesHashAndCreatorSurviveReloadAndUnrelatedDocumentEdit() throws Exception {
        String w=workspace();var response=create(w);var memory=entry(response);
        assertEquals("workspace",memory.get("scope"));assertEquals(alice.id(),memory.get("created_by"));assertEquals("active",memory.get("status"));
        var source=(Map<?,?>)memory.get("source");assertEquals(Requests.sha256(BODY),source.get("document_sha256"));assertEquals(w+":1",source.get("snapshot_id"));
        store.putDocument(w,"other",new Requests.Document("Other","unrelated","fixture://other"));
        mvc.perform(as(get("/api/workspaces/"+w+"/memories"),alice)).andExpect(status().isOk()).andExpect(jsonPath("revision").value(1)).andExpect(jsonPath("memories[0].status").value("active"));
    }
    @Test void duplicateKeyRequiresExplicitVersionedReplacementWithoutOverwritingCreator() {
        String w=workspace();String id=id(create(w));
        assertEquals("memory_conflict",assertThrows(ApiError.class,()->memories.write(w,null,input("baseline",1,null),alice)).code);
        assertEquals("stale_memory",assertThrows(ApiError.class,()->memories.write(w,id,input("baseline",1,2L),alice)).code);
        auth.setMember(w,bobName,"EDITOR",alice);
        var updated=memories.write(w,id,input("baseline",1,1L),bob);
        assertEquals(2L,updated.get("revision"));assertEquals(2L,entry(updated).get("version"));assertEquals(alice.id(),entry(updated).get("created_by"));
    }
    @Test void sourceVersionHashAndDeletionInvalidateUntilExplicitReplacement() {
        String w=workspace();String id=id(create(w));
        store.putDocument(w,"source",new Requests.Document("Source",BODY,"fixture://source")); // Same text, new version still stale.
        assertEquals("stale",((Map<?,?>)((List<?>)memories.list(w).get("memories")).getFirst()).get("status"));
        assertEquals("memory_source_stale",assertThrows(ApiError.class,()->memories.write(w,id,input("baseline",1,1L),alice)).code);
        var replacement=new MemoryRequests.Mutation("baseline","source",2L,"SFT baseline 95.02 percent.","reviewed",null,1L,1L);
        assertEquals("active",entry(memories.write(w,id,replacement,alice)).get("status"));
        db.update("UPDATE documents SET body='tampered body' WHERE workspace_id=? AND id='source'",w);
        assertEquals("stale",((Map<?,?>)((List<?>)memories.list(w).get("memories")).getFirst()).get("status"));
        store.deleteDocument(w,"source");assertEquals("stale",((Map<?,?>)((List<?>)memories.list(w).get("memories")).getFirst()).get("status"));
    }
    @Test void rejectsInexactQuoteMissingSourceAndInvalidExpiryWithNoMutation() {
        String w=workspace();
        var inaccurate=new MemoryRequests.Mutation("baseline","source",1L,"95.03",null,null,0L,null);
        assertEquals("invalid_request",assertThrows(ApiError.class,()->memories.write(w,null,inaccurate,alice)).code);
        for (String expiry:List.of(Instant.now().minusSeconds(1).toString(),Instant.now().plus(366,ChronoUnit.DAYS).toString())) {
            var value=new MemoryRequests.Mutation("baseline","source",1L,"SFT",null,expiry,0L,null);
            assertThrows(ApiError.class,()->memories.write(w,null,value,alice));
        }
        assertEquals(0L,memories.list(w).get("revision"));assertEquals(List.of(),memories.list(w).get("memories"));
    }
    @Test void expiryIsDerivedFromDatabaseTimeAndPreventsNewSelection() {
        String w=workspace();String id=id(create(w));
        db.update("UPDATE workspace_memories SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE id=?",id);
        assertEquals("expired",((Map<?,?>)((List<?>)memories.list(w).get("memories")).getFirst()).get("status"));
        assertEquals("memory_unavailable",assertThrows(ApiError.class,()->store.submit(w,"expired",selected(id,1L))).code);
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,w));
    }
    @Test void allNewRoutesRespectOwnerEditorViewerCsrfAndWorkspaceIsolation() throws Exception {
        String w=workspace(),other=workspace();String id=id(create(w));String path="/api/workspaces/"+w+"/memories";
        mvc.perform(get(path)).andExpect(status().isUnauthorized());
        mvc.perform(as(get(path),bob)).andExpect(status().isNotFound());
        auth.setMember(w,bobName,"VIEWER",alice);
        mvc.perform(as(get(path),bob)).andExpect(status().isOk());
        mvc.perform(as(post(path),bob).contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(input("another",1,null)))).andExpect(status().isForbidden());
        mvc.perform(as(put(path+"/"+id),bob).contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(input("baseline",1,1L)))).andExpect(status().isForbidden());
        mvc.perform(as(delete(path+"/"+id),bob).contentType(MediaType.APPLICATION_JSON).content("{\"expected_revision\":1,\"expected_version\":1}")).andExpect(status().isForbidden());
        auth.setMember(w,bobName,"EDITOR",alice);
        mvc.perform(post(path).cookie(new Cookie(AuthFilter.COOKIE,bob.token())).header("Origin","http://127.0.0.1:8086").contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(input("another",1,null)))).andExpect(status().isForbidden());
        mvc.perform(as(post(path),bob).contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(input("another",1,null)))).andExpect(status().isCreated());
        mvc.perform(as(delete("/api/workspaces/"+other+"/memories/"+id),alice).contentType(MediaType.APPLICATION_JSON).content("{\"expected_revision\":0,\"expected_version\":1}")).andExpect(status().isNotFound());
    }
    @Test void directMutationAlsoRechecksMembershipAndDisallowsAnonymousDevelopmentWrites() {
        String w=workspace();assertEquals("authentication_required",assertThrows(ApiError.class,()->memories.write(w,null,input("baseline",0,null),null)).code);
        assertEquals("not_found",assertThrows(ApiError.class,()->memories.write(w,null,input("baseline",0,null),bob)).code);
        auth.setMember(w,bobName,"VIEWER",alice);assertEquals("access_denied",assertThrows(ApiError.class,()->memories.write(w,null,input("baseline",0,null),bob)).code);
    }
    @Test void selectedMemoryRequiresExactApprovalAndExistingActiveWorkspaceEntries() {
        String w=workspace(),other=workspace();String id=id(create(w));
        assertEquals("memory_approval_required",assertThrows(ApiError.class,()->store.submit(w,"missing",selected(id,null))).code);
        assertEquals("stale_memory_revision",assertThrows(ApiError.class,()->store.submit(w,"stale",selected(id,0L))).code);
        assertEquals("memory_unavailable",assertThrows(ApiError.class,()->store.submit(other,"foreign",selected(id,0L))).code);
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id IN (?,?)",Integer.class,w,other));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id IN (?,?)",Integer.class,w,other));
    }
    @Test void approvedSelectionIsImmutableRetainedAfterDeleteAndReplayDoesNotReattachNewMemory() {
        String w=workspace();String id=id(create(w));var request=selected(id,1L);var accepted=store.submit(w,"once",request);
        String task=(String)accepted.task().get("id");var original=snapshot(task);
        assertFalse(original.containsKey("memory_ids"));assertFalse(original.containsKey("approved_memory_revision"));assertTrue(original.containsKey("memory_context"));
        memories.delete(w,id,new MemoryRequests.Delete(1L,1L),alice);
        assertEquals(List.of(),memories.list(w).get("memories"));assertEquals(original,snapshot(task));assertTrue(store.submit(w,"once",request).reused());
        assertEquals("stale_memory_revision",assertThrows(ApiError.class,()->store.submit(w,"new",request)).code);
        var plain=store.submit(w,"plain",new Requests.Task("q","bm25","agent",3,1L));assertFalse(snapshot((String)plain.task().get("id")).containsKey("memory_context"));
    }
    @Test void memoryTaskUsesExistingGlobalQuotaAndCannotBypassDocumentConsent() {
        String w=workspace();String id=id(create(w));
        var noConsent=new Requests.Task("q","bm25","agent",3,null,List.of(id),1L);
        assertEquals("approval_required",assertThrows(ApiError.class,()->store.submit(w,"unapproved",noConsent)).code);
        store.submit(w,"one",selected(id,1L));store.submit(w,"two",selected(id,1L));
        assertEquals("global_queue_full",assertThrows(ApiError.class,()->store.submit(w,"three",selected(id,1L))).code);
    }
    @Test void conversationSelectionUsesSameValidationAndSnapshot() {
        String w=workspace();String id=id(create(w));String c=(String)store.createConversation(w,new Requests.Conversation("Memory conversation",null,null,null)).get("id");
        var t=new Requests.Turn("q","bm25","agent",3,1L,0L,null,List.of(id),1L);
        var accepted=store.submitTurn(w,c,"turn",t);var saved=snapshot((String)accepted.task().get("id"));
        assertTrue(saved.containsKey("conversation_context"));assertTrue(saved.containsKey("memory_context"));
        assertEquals(List.of(id),((Map<?,?>)accepted.task().get("request")).get("memory_ids"));
    }
    @Test void concurrentMutationsProduceOneWinnerAndNoSilentOverwrite() throws Exception {
        String w=workspace();
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1);var tasks=new ArrayList<Future<String>>();
            for (int i=0;i<2;i++) {String key="key-"+i;tasks.add(pool.submit(()->{gate.await();try {memories.write(w,null,input(key,0,null),alice);return "created";}catch(ApiError e){return e.code;}}));}
            gate.countDown();var outcomes=new HashSet<String>();for(var task:tasks)outcomes.add(task.get(10,TimeUnit.SECONDS));
            assertEquals(Set.of("created","stale_memory_revision"),outcomes);assertEquals(1,((List<?>)memories.list(w).get("memories")).size());
        }
    }
    @Test void concurrentSourceChangeCannotApproveNewTaskWithStaleMemory() throws Exception {
        String w=workspace();String id=id(create(w));
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1);
            var submit=pool.submit(()->{gate.await();try{return store.submit(w,"race",selected(id,1L)).task();}catch(ApiError e){return Map.<String,Object>of("error",e.code);}});
            var change=pool.submit(()->{gate.await();return store.putDocument(w,"source",new Requests.Document("New","changed source","fixture://source"));});
            gate.countDown();var result=submit.get(10,TimeUnit.SECONDS);change.get(10,TimeUnit.SECONDS);
            if(result.containsKey("error")) assertEquals("stale_approval",result.get("error"));
            else {var saved=snapshot((String)result.get("id"));assertEquals(w+":1",saved.get("snapshot_id"));assertTrue(saved.containsKey("memory_context"));}
        }
    }
    @Test void storedCapacityIncludesExpiredRecordsAndDeleteReleasesCapacity() {
        String w=workspace();String first=id(create(w));
        for(int i=1;i<100;i++)memories.write(w,null,input("key-"+i,i,null),alice);
        db.update("UPDATE workspace_memories SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE workspace_id=?",w);
        assertEquals("memory_capacity",assertThrows(ApiError.class,()->memories.write(w,null,input("overflow",100,null),alice)).code);
        memories.delete(w,first,new MemoryRequests.Delete(100L,1L),alice);
        assertEquals(102L,memories.write(w,null,input("replacement",101,null),alice).get("revision"));
    }
    @Test void memoryReplacementAndSelectionSerializeWithoutMixingApprovedVersions() throws Exception {
        String w=workspace();String id=id(create(w));
        try (var pool=Executors.newFixedThreadPool(2)) {
            var gate=new CountDownLatch(1);
            var submit=pool.submit(()->{gate.await();try{return store.submit(w,"memory-race",selected(id,1L)).task();}catch(ApiError e){return Map.<String,Object>of("error",e.code);}});
            var change=pool.submit(()->{gate.await();return memories.write(w,id,input("baseline",1,1L),alice);});
            gate.countDown();var result=submit.get(10,TimeUnit.SECONDS);change.get(10,TimeUnit.SECONDS);
            if(result.containsKey("error")) assertEquals("stale_memory_revision",result.get("error"));
            else {
                var context=(Map<?,?>)snapshot((String)result.get("id")).get("memory_context");
                assertEquals(1,((Number)context.get("revision")).intValue());
                assertEquals(1,((Number)((Map<?,?>)((List<?>)context.get("entries")).getFirst()).get("version")).intValue());
            }
        }
    }
    @Test void escapedUtf8ContextLimitIsCheckedBeforeTaskAndOutboxCreation() {
        String w=workspace(),quote="x"+"\u0001".repeat(999),note="\u0001".repeat(500);
        store.putDocument(w,"source",new Requests.Document("Escaped source",quote,"fixture://source"));
        var ids=new ArrayList<String>();
        for(int i=0;i<8;i++) ids.add(id(memories.write(w,null,new MemoryRequests.Mutation("key-"+i,"source",2L,quote,note,null,(long)i,null),alice)));
        var request=new Requests.Task("q","bm25","agent",3,2L,ids,8L);
        assertEquals("memory_context_capacity",assertThrows(ApiError.class,()->store.submit(w,"too-large",request)).code);
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,w));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id=?",Integer.class,w));
    }
}
