package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** Real MySQL transactions and indexes. Controlled Redis gates isolate ordering/race boundaries. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.rate-limit=10000",
    "lablens.max-pending-global=4","lablens.max-pending-tasks=2","lablens.cache-namespace=round12-admission-tests",
    "lablens.mq.enabled=true","lablens.mq.relay-enabled=false","lablens.mq.consumer-auto-startup=false"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class AdmissionIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired TaskAdmissionStore admission;
    @Autowired TaskEventStore events;
    @Autowired JdbcTemplate db;
    @Autowired TransactionTemplate tx;
    @Autowired RedisSupport redis;
    @Autowired StringRedisTemplate redisTemplate;
    @Autowired MeterRegistry metrics;
    @Autowired ObjectMapper json;
    @Autowired MockMvc mvc;
    private final List<String> workspaces=new ArrayList<>();

    @BeforeEach void isolatedDatabaseHasNoActiveTasks() {
        assertEquals(0,active(),"Integration tests require an isolated database with no concurrently running service");
    }
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("admission-test-"+UUID.randomUUID())).get("id");
        workspaces.add(id);
        store.putDocument(id,"evidence",new Requests.Document("Evidence","Accuracy is 86 percent.","test://admission"));
        return id;
    }
    private Requests.Task request() { return new Requests.Task("What is the accuracy?","bm25","extractive",1); }
    private Requests.Task provider() { return new Requests.Task("What is the accuracy?","bm25","agent",1,1L); }
    private String submit(String workspace,String key) { return (String)store.submit(workspace,key,request()).task().get("id"); }
    private int active() { return db.queryForObject("SELECT COUNT(*) FROM tasks WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",Integer.class); }
    private int outbox(String workspace) { return db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id=?",Integer.class,workspace); }
    private WorkspaceStore withGate(RedisSupport gate) { return new WorkspaceStore(db,tx,json,gate,metrics,admission,events); }
    private RedisSupport fixedGate(RedisSupport.AdmissionDecision decision) {
        return new RedisSupport(redisTemplate,metrics,10000,300,"controlled-gate") {
            @Override public AdmissionDecision allowSubmission(String workspace) { return decision; }
        };
    }
    private TransactionTemplate readCommitted() {
        TransactionTemplate result=new TransactionTemplate(tx.getTransactionManager());
        result.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        return result;
    }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM conversations WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }

    @Test void concurrentDifferentWorkspacesCannotExceedDatabaseWideCapacity() throws Exception {
        List<String> ids=new ArrayList<>(); for (int i=0;i<16;i++) ids.add(workspace());
        try (var pool=Executors.newFixedThreadPool(16)) {
            var gate=new CountDownLatch(1); List<Future<String>> results=new ArrayList<>();
            for (String id:ids) results.add(pool.submit(()->{
                gate.await();
                try { store.submit(id,"competing",request()); return "accepted"; }
                catch (ApiError error) { assertEquals(503,error.status.value()); return error.code; }
            }));
            gate.countDown(); Map<String,Integer> counts=new HashMap<>();
            for (var result:results) counts.merge(result.get(15,TimeUnit.SECONDS),1,Integer::sum);
            assertEquals(Map.of("accepted",4,"global_queue_full",12),counts);
            assertEquals(4,active());
            assertEquals(4,ids.stream().mapToInt(this::outbox).sum());
        }
    }

    @Test void workspaceCapIsIndependentAndQueuedCancellationReleasesCapacity() {
        String a=workspace(),b=workspace(); String cancelled=submit(a,"one"); submit(a,"two"); submit(b,"other");
        ApiError error=assertThrows(ApiError.class,()->store.submit(a,"retryable",request()));
        assertEquals("queue_full",error.code); assertEquals(429,error.status.value());
        assertEquals(2,outbox(a));
        store.cancel(a,cancelled);
        assertFalse(store.submit(a,"retryable",request()).reused());
        assertEquals(3,active()); assertEquals(3,outbox(a));
    }

    @Test void runningAndCancellationPendingRetainSlotsUntilAcknowledgementOrCompletion() {
        String a=workspace(),b=workspace(),c=workspace(),d=workspace(),e=workspace();
        String one=submit(a,"one"); WorkspaceStore.Claimed first=store.claim("worker",60); assertEquals(one,first.id());
        String two=submit(b,"two"); WorkspaceStore.Claimed second=store.claim("worker",60); assertEquals(two,second.id());
        store.cancel(b,two); submit(c,"three"); submit(d,"four");
        assertEquals("global_queue_full",assertThrows(ApiError.class,()->store.submit(e,"next",request())).code);
        store.cancel(a,one);
        assertEquals(4,active());
        assertEquals("global_queue_full",assertThrows(ApiError.class,()->store.submit(e,"next",request())).code);
        assertTrue(store.acknowledgeCancellation(two,WorkerResults.zeroUsage(false)));
        submit(e,"next"); assertEquals(4,active());
        var running=store.claim("completion",60);
        assertTrue(store.finish(running,"completion","{}",store.encode(WorkerResults.zeroUsage(false)),false,1));
        assertFalse(store.submit(b,"after-completion",request()).reused());
        assertEquals(4,active());
    }

    @Test void fullQueueReplaysAndConflictsNeverContactRedis() {
        String a=workspace(),b=workspace(); String original=submit(a,"same"); submit(a,"second"); submit(b,"third"); submit(b,"fourth");
        WorkspaceStore isolated=withGate(new RedisSupport(redisTemplate,metrics,1,1,"never-called") {
            @Override public AdmissionDecision allowSubmission(String workspace) { throw new AssertionError("Durable replay must skip Redis"); }
        });
        var replay=isolated.submit(a,"same",request());
        assertTrue(replay.reused()); assertEquals(original,replay.task().get("id"));
        assertEquals("idempotency_conflict",assertThrows(ApiError.class,()->isolated.submit(a,"same",new Requests.Task("changed","bm25","extractive",1))).code);
        assertEquals(4,active()); assertEquals(2,outbox(a));
    }

    @Test void databaseRollbackRestoresCapacityOutboxAndAcceptanceCounters() {
        String a=workspace(),b=workspace(),c=workspace(); submit(a,"one"); submit(a,"two"); submit(b,"three");
        double before=metrics.counter("lablens.admission.outcomes","outcome","accepted","reason","none").count();
        readCommitted().executeWithoutResult(transaction->{
            submit(c,"rolled-back"); assertEquals(4,active()); assertEquals(1,outbox(c));
            transaction.setRollbackOnly();
        });
        assertEquals(3,active()); assertEquals(0,outbox(c));
        assertEquals(before,metrics.counter("lablens.admission.outcomes","outcome","accepted","reason","none").count());
        assertFalse(store.submit(c,"rolled-back",request()).reused()); assertEquals(4,active());
    }

    @Test void blockedGuardWaiterSeesPreviousAdmissionCommitUnderReadCommitted() throws Exception {
        String a=workspace(),b=workspace(),c=workspace(); submit(a,"one"); submit(a,"two"); submit(b,"three");
        var holding=new CountDownLatch(1); var release=new CountDownLatch(1);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var holder=pool.submit(()->readCommitted().executeWithoutResult(transaction->{
                submit(b,"last-slot"); holding.countDown();
                try { assertTrue(release.await(5,TimeUnit.SECONDS)); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new IllegalStateException(error); }
            }));
            try {
                assertTrue(holding.await(3,TimeUnit.SECONDS));
                var waiter=pool.submit(()->store.submit(c,"waiter",request()));
                assertThrows(TimeoutException.class,()->waiter.get(100,TimeUnit.MILLISECONDS));
                release.countDown(); holder.get(3,TimeUnit.SECONDS);
                ExecutionException error=assertThrows(ExecutionException.class,()->waiter.get(3,TimeUnit.SECONDS));
                assertEquals("global_queue_full",((ApiError)error.getCause()).code);
                assertEquals(4,active()); assertEquals(0,outbox(c));
            } finally { release.countDown(); }
        }
    }

    @Test void redisDecisionDoesNotHoldGlobalOrWorkspaceLockAndRejectedRaceRechecksReplay() throws Exception {
        String a=workspace(); var checking=new CountDownLatch(1); var release=new CountDownLatch(1);
        WorkspaceStore delayed=withGate(new RedisSupport(redisTemplate,metrics,1,1,"delayed") {
            @Override public AdmissionDecision allowSubmission(String workspace) {
                checking.countDown();
                try { assertTrue(release.await(5,TimeUnit.SECONDS)); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new IllegalStateException(error); }
                return AdmissionDecision.LIMITED;
            }
        });
        try (var pool=Executors.newFixedThreadPool(2)) {
            var delayedRequest=pool.submit(()->delayed.submit(a,"racing",request()));
            try {
                assertTrue(checking.await(3,TimeUnit.SECONDS));
                // Same workspace and same global guard would block here if Redis held either lock.
                var winner=pool.submit(()->store.submit(a,"racing",request())).get(2,TimeUnit.SECONDS);
                release.countDown(); var replay=delayedRequest.get(2,TimeUnit.SECONDS);
                assertTrue(replay.reused()); assertEquals(winner.task().get("id"),replay.task().get("id"));
                assertEquals(1,active()); assertEquals(1,outbox(a));
            } finally { release.countDown(); }
        }
    }

    @Test void unavailableGateRejectsEveryProviderRouteButOfflineTaskUsesDatabaseCapacity() {
        String a=workspace(); WorkspaceStore unavailable=withGate(fixedGate(RedisSupport.AdmissionDecision.UNAVAILABLE));
        for (String[] route:new String[][]{{"bm25","agent"},{"bm25","live"},{"dense","extractive"},{"hybrid","extractive"}}) {
            ApiError error=assertThrows(ApiError.class,()->unavailable.submit(a,route[0]+route[1],new Requests.Task("q",route[0],route[1],1,1L)));
            assertEquals("admission_unavailable",error.code); assertEquals(503,error.status.value());
        }
        assertEquals(0,outbox(a));
        var offline=unavailable.submit(a,"offline",request()); assertFalse(offline.reused());
        var approved=store.submit(a,"approved",provider());
        assertEquals(approved.task().get("id"),unavailable.submit(a,"approved",provider()).task().get("id"));
        assertEquals("queue_full",assertThrows(ApiError.class,()->unavailable.submit(a,"full",request())).code);
        assertEquals(2,outbox(a));
    }

    @Test void staleOuterTransactionIsRejectedBeforeRedisAndNeverConsumesCapacity() {
        String a=workspace(); var calls=new AtomicInteger();
        WorkspaceStore observed=withGate(new RedisSupport(redisTemplate,metrics,1,1,"observed") {
            @Override public AdmissionDecision allowSubmission(String workspace) { calls.incrementAndGet(); return AdmissionDecision.ALLOWED; }
        });
        TransactionTemplate stale=new TransactionTemplate(tx.getTransactionManager());
        stale.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);
        assertThrows(IllegalStateException.class,()->stale.executeWithoutResult(transaction->observed.submit(a,"stale",request())));
        assertEquals(0,calls.get()); assertEquals(0,active()); assertEquals(0,outbox(a));
    }

    @Test void rejectedConversationDoesNotAdvanceRevisionOrStateAndCanRetryAfterDrain() {
        String a=workspace(),b=workspace(),c=workspace(); submit(a,"one"); submit(a,"two"); submit(b,"three"); submit(b,"four");
        String conversation=(String)store.createConversation(c,new Requests.Conversation("Capacity",null,null,null)).get("id");
        var turn=new Requests.Turn("q","bm25","agent",1,1L,0L,new Requests.State("Changed",List.of("Evidence only"),"SFT"));
        assertEquals("global_queue_full",assertThrows(ApiError.class,()->store.submitTurn(c,conversation,"retry",turn)).code);
        assertEquals(0L,store.conversation(c,conversation).get("revision"));
        assertEquals("",((Map<?,?>)store.conversation(c,conversation).get("state")).get("goal"));
        assertEquals(0,outbox(c));
        var first=store.tasks(a).getFirst(); store.cancel(a,(String)first.get("id"));
        assertFalse(store.submitTurn(c,conversation,"retry",turn).reused());
        assertTrue(store.submitTurn(c,conversation,"retry",turn).reused());
        assertEquals(1L,store.conversation(c,conversation).get("revision")); assertEquals(1,outbox(c));
    }

    @Test void httpOverloadContractDistinguishesWorkspaceAndGlobalCapacity() throws Exception {
        String a=workspace(),b=workspace(),c=workspace(); submit(a,"one"); submit(a,"two");
        String body=store.encode(request().fields());
        mvc.perform(post("/api/workspaces/"+a+"/tasks").header("Idempotency-Key","full").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isTooManyRequests()).andExpect(header().string("Retry-After","1"))
            .andExpect(jsonPath("error").value("queue_full")).andExpect(jsonPath("retryable").value(true));
        submit(b,"three"); submit(b,"four");
        mvc.perform(post("/api/workspaces/"+c+"/tasks").header("Idempotency-Key","full").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isServiceUnavailable()).andExpect(header().string("Retry-After","1"))
            .andExpect(jsonPath("error").value("global_queue_full")).andExpect(jsonPath("retry_after_seconds").value(1));
        mvc.perform(post("/api/workspaces/"+a+"/tasks").header("Idempotency-Key","one").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isOk()).andExpect(jsonPath("reused").value(true));
    }

    @Test void statusReportsBoundedGlobalScopeAndOldestQueuedAgeWithoutPayloads() {
        String a=workspace(); String one=submit(a,"aged");
        db.update("UPDATE tasks SET created_at=TIMESTAMPADD(SECOND,-5,UTC_TIMESTAMP(6)) WHERE id=?",one);
        Map<String,Object> status=admission.status();
        assertEquals("database_all_workspaces",status.get("scope"));
        assertEquals(4,status.get("global_pending_limit")); assertEquals(2,status.get("workspace_pending_limit"));
        assertEquals(1,status.get("pending_global")); assertEquals(false,status.get("pending_global_capped"));
        assertTrue(((Number)status.get("oldest_queued_age_ms")).longValue()>=5000);
        String encoded=store.encode(status); assertFalse(encoded.contains(a)); assertFalse(encoded.contains("Accuracy"));
    }
}
