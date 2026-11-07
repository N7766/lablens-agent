package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** Real MySQL/Redis only; broker transport is covered independently by TaskMqIntegrationTest. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.rate-limit=10000","lablens.mq.enabled=true",
    "lablens.mq.relay-enabled=false","lablens.mq.consumer-auto-startup=false"})
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class TaskEventStoreIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired TaskEventStore events;
    @Autowired JdbcTemplate db;
    @Autowired TransactionTemplate tx;
    private final List<String> workspaces=new ArrayList<>();
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("outbox-test-"+UUID.randomUUID())).get("id");
        workspaces.add(id); store.putDocument(id,"evidence",new Requests.Document("Evidence","Do not place this document body in a message.","test://evidence")); return id;
    }
    private Requests.Task request() { return new Requests.Task("What does the evidence say?","bm25","extractive",1); }
    private String submit(String workspace,String key) { return (String)store.submit(workspace,key,request()).task().get("id"); }
    private TaskEventStore.Event event(String task) {
        return db.queryForObject("SELECT * FROM task_event_outbox WHERE task_id=?",(rs,n)->new TaskEventStore.Event(
            rs.getString("event_id"),rs.getString("task_id"),rs.getString("workspace_id"),rs.getString("snapshot_ref"),rs.getInt("schema_version"),rs.getString("event_type")),task);
    }
    private String state(String task) { return db.queryForObject("SELECT state FROM task_event_outbox WHERE task_id=?",String.class,task); }
    private void expire(TaskEventStore.Claimed claim) {
        db.update("UPDATE task_event_outbox SET lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)),next_attempt_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE event_id=?",claim.event().eventId());
    }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            // FK cascades retain no orphan inbox/outbox after test task deletion.
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }

    @Test void taskAndOutboxCommitOrRollbackTogether() {
        String workspace=workspace();
        TransactionTemplate submissionTx=new TransactionTemplate(tx.getTransactionManager());
        submissionTx.setIsolationLevel(org.springframework.transaction.TransactionDefinition.ISOLATION_READ_COMMITTED);
        submissionTx.executeWithoutResult(status->{
            String task=submit(workspace,"rolled-back");
            assertNotNull(event(task));
            status.setRollbackOnly();
        });
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,workspace));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id=?",Integer.class,workspace));
        String task=submit(workspace,"committed");
        TaskEventStore.Event event=event(task);
        assertEquals(workspace+":1",event.snapshotRef());
        assertEquals(TaskEventStore.EVENT_TYPE,event.eventType());
        assertEquals(1,event.schemaVersion());
        assertFalse(store.encode(event).contains("document body"));
        assertFalse(store.encode(event).contains("What does"));
    }

    @Test void enqueueRequiresAnActualSubmissionTransactionAndOffDoesNotCapture() {
        String workspace=workspace(),task=submit(workspace,"enabled");
        assertThrows(IllegalStateException.class,()->events.enqueue(task,workspace,1));
        int before=db.queryForObject("SELECT COUNT(*) FROM task_event_outbox",Integer.class);
        new TaskEventStore(db,tx,false).enqueue(task,workspace,1);
        assertEquals(before,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox",Integer.class));
    }

    @Test void concurrentIdempotentSubmissionCreatesOneTaskAndOneNotification() throws Exception {
        String workspace=workspace();
        try (var pool=Executors.newFixedThreadPool(8)) {
            var start=new CountDownLatch(1); var futures=new ArrayList<Future<WorkspaceStore.Submission>>();
            for (int i=0;i<8;i++) futures.add(pool.submit(()->{start.await();return store.submit(workspace,"same",request());}));
            start.countDown(); Set<Object> ids=new HashSet<>(); int fresh=0;
            for (var result:futures) { var value=result.get(10,TimeUnit.SECONDS); ids.add(value.task().get("id")); if (!value.reused()) fresh++; }
            assertEquals(1,ids.size()); assertEquals(1,fresh);
            assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id=?",Integer.class,workspace));
        }
    }

    @Test void concurrentPublishersClaimDistinctEvents() throws Exception {
        String workspace=workspace();
        for (int i=0;i<12;i++) submit(workspace,"claim-"+i);
        try (var pool=Executors.newFixedThreadPool(6)) {
            var gate=new CountDownLatch(1); var claims=new ArrayList<Future<TaskEventStore.Claimed>>();
            for (int i=0;i<12;i++) { final int owner=i; claims.add(pool.submit(()->{gate.await();return events.claim("owner-"+owner,30,3).orElseThrow();})); }
            gate.countDown(); Set<String> ids=new HashSet<>(); Set<String> tokens=new HashSet<>();
            for (var result:claims) { var claim=result.get(10,TimeUnit.SECONDS); assertEquals(1,claim.attempts()); ids.add(claim.event().eventId()); tokens.add(claim.leaseToken()); }
            assertEquals(12,ids.size()); assertEquals(12,tokens.size());
            assertTrue(events.claim("extra",30,3).isEmpty());
        }
    }

    @Test void skipLockedClaimDoesNotWaitForAnotherPublisherTransaction() throws Exception {
        String workspace=workspace(); String a=submit(workspace,"locked"),b=submit(workspace,"free");
        var locked=new CountDownLatch(1); var release=new CountDownLatch(1);
        try (var pool=Executors.newFixedThreadPool(2)) {
            var holder=pool.submit(()->tx.executeWithoutResult(status->{
                db.queryForObject("SELECT event_id FROM task_event_outbox WHERE task_id=? FOR UPDATE",String.class,a); locked.countDown();
                try { release.await(5,TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
            }));
            try {
                assertTrue(locked.await(3,TimeUnit.SECONDS));
                var result=pool.submit(()->events.claim("other",30,3).orElseThrow()).get(2,TimeUnit.SECONDS);
                assertEquals(b,result.event().taskId());
            } finally { release.countDown(); holder.get(3,TimeUnit.SECONDS); }
        }
    }

    @Test void expiredLeaseAndSameOwnerReclaimFenceAllOldConfirmAndRetryWrites() {
        String workspace=workspace(),task=submit(workspace,"lease");
        var old=events.claim("same-owner",30,3).orElseThrow(); expire(old);
        assertFalse(events.published(old,"same-owner"));
        assertFalse(events.retry(old,"same-owner","confirm_timeout",3));
        var fresh=events.claim("same-owner",30,3).orElseThrow();
        assertEquals(old.event(),fresh.event()); assertNotEquals(old.leaseToken(),fresh.leaseToken()); assertEquals(2,fresh.attempts());
        assertFalse(events.published(old,"same-owner")); assertFalse(events.retry(old,"same-owner","confirm_timeout",3));
        assertFalse(events.published(fresh,"wrong-owner"));
        assertTrue(events.published(fresh,"same-owner")); assertEquals("PUBLISHED",state(task));
        assertFalse(events.published(fresh,"same-owner"));
    }

    @Test void retryBackoffAndMaximumAttemptsEndInAuditableDeadState() {
        String workspace=workspace(),task=submit(workspace,"retry");
        var first=events.claim("owner",30,2).orElseThrow();
        assertTrue(events.retry(first,"owner","confirm_timeout",2)); assertEquals("RETRY",state(task));
        assertTrue(events.claim("owner",30,2).isEmpty(),"Retry must respect persisted backoff");
        assertTrue(db.queryForObject("SELECT next_attempt_at>UTC_TIMESTAMP(6) FROM task_event_outbox WHERE task_id=?",Boolean.class,task));
        db.update("UPDATE task_event_outbox SET next_attempt_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE task_id=?",task);
        var second=events.claim("owner",30,2).orElseThrow(); assertEquals(2,second.attempts());
        assertTrue(events.retry(second,"owner","unsafe provider body / token",2)); assertEquals("DEAD",state(task));
        assertEquals("publish_failed",db.queryForObject("SELECT last_error FROM task_event_outbox WHERE task_id=?",String.class,task));
        assertNull(db.queryForObject("SELECT next_attempt_at FROM task_event_outbox WHERE task_id=?",java.sql.Timestamp.class,task));
        assertTrue(events.claim("owner",30,2).isEmpty());
        assertEquals("QUEUED",store.task(workspace,task).get("status"));
    }

    @Test void crashOnFinalPublisherAttemptExpiresToDeadWithoutAnotherSend() {
        String workspace=workspace(),task=submit(workspace,"last-crash");
        var first=events.claim("crashed",30,1).orElseThrow(); expire(first);
        assertTrue(events.claim("replacement",30,1).isEmpty()); assertEquals("DEAD",state(task));
        assertEquals(1,db.queryForObject("SELECT attempts FROM task_event_outbox WHERE task_id=?",Integer.class,task));
        assertEquals("attempts_exhausted",db.queryForObject("SELECT last_error FROM task_event_outbox WHERE task_id=?",String.class,task));
    }

    @Test void validatesEveryImmutableFieldEvenAfterInboxAlreadyContainsTheId() {
        String workspace=workspace(),task=submit(workspace,"identity"); var original=event(task);
        assertEquals(TaskEventStore.Acceptance.ACCEPTED,events.accept(original));
        List<TaskEventStore.Event> invalid=List.of(
            new TaskEventStore.Event(UUID.randomUUID().toString(),task,workspace,original.snapshotRef(),1,original.eventType()),
            new TaskEventStore.Event(original.eventId(),UUID.randomUUID().toString(),workspace,original.snapshotRef(),1,original.eventType()),
            new TaskEventStore.Event(original.eventId(),task,UUID.randomUUID().toString(),original.snapshotRef(),1,original.eventType()),
            new TaskEventStore.Event(original.eventId(),task,workspace,workspace+":2",1,original.eventType()),
            new TaskEventStore.Event(original.eventId(),task,workspace,original.snapshotRef(),2,original.eventType()),
            new TaskEventStore.Event(original.eventId(),task,workspace,original.snapshotRef(),1,"other"));
        for (var candidate:invalid) assertThrows(IllegalArgumentException.class,()->events.accept(candidate));
        assertEquals(TaskEventStore.Acceptance.DUPLICATE,events.accept(original));
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM task_event_inbox WHERE task_id=?",Integer.class,task));
    }

    @Test void simultaneousConsumerDuplicatesPersistOnlyOneInboxReceipt() throws Exception {
        String workspace=workspace(),task=submit(workspace,"inbox"); var event=event(task);
        try (var pool=Executors.newFixedThreadPool(8)) {
            var gate=new CountDownLatch(1); var futures=new ArrayList<Future<TaskEventStore.Acceptance>>();
            for (int i=0;i<8;i++) futures.add(pool.submit(()->{gate.await();return events.accept(event);})); gate.countDown();
            int accepted=0,duplicates=0;
            for (var result:futures) { var value=result.get(10,TimeUnit.SECONDS); if (value==TaskEventStore.Acceptance.ACCEPTED) accepted++; if (value==TaskEventStore.Acceptance.DUPLICATE) duplicates++; }
            assertEquals(1,accepted); assertEquals(7,duplicates);
            assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM task_event_inbox WHERE task_id=?",Integer.class,task));
        }
    }

    @Test void inboxInsertRollsBackWithItsTransaction() {
        String workspace=workspace(),task=submit(workspace,"inbox-rollback"); var event=event(task);
        tx.executeWithoutResult(status->{ assertEquals(TaskEventStore.Acceptance.ACCEPTED,events.accept(event)); status.setRollbackOnly(); });
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_inbox WHERE task_id=?",Integer.class,task));
        assertEquals(TaskEventStore.Acceptance.ACCEPTED,events.accept(event));
    }

    @Test void notificationsNeverResetRunningOrTerminalTaskState() {
        String workspace=workspace();
        for (String state:List.of("RUNNING","FAILED","CANCELLED","CANCEL_REQUESTED","SUCCEEDED")) {
            String task=submit(workspace,"state-"+state);
            db.update("UPDATE tasks SET status=?,usage_unknown=TRUE,error_code='known_marker' WHERE id=?",state,task);
            assertEquals(TaskEventStore.Acceptance.NOT_QUEUED,events.accept(event(task)));
            var unchanged=store.task(workspace,task);
            assertEquals(state,unchanged.get("status")); assertEquals(true,unchanged.get("usage_unknown")); assertEquals("known_marker",unchanged.get("error_code"));
            assertEquals(TaskEventStore.Acceptance.DUPLICATE,events.accept(event(task)));
        }
    }

    @Test void expiredLiveTaskUnknownUsageRemainsFailedAfterDelayedNotification() {
        String workspace=workspace();
        String task=(String)store.submit(workspace,"interrupted",new Requests.Task("q","bm25","agent",1,1L)).task().get("id");
        db.update("UPDATE tasks SET status='RUNNING',owner='lost-worker',lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=?",task);
        assertTrue(store.expireLeases()>=1);
        var before=store.task(workspace,task);
        assertEquals("FAILED",before.get("status")); assertEquals(true,before.get("usage_unknown"));
        assertEquals(TaskEventStore.Acceptance.NOT_QUEUED,events.accept(event(task)));
        var after=store.task(workspace,task);
        assertEquals(before.get("execution_usage"),after.get("execution_usage"));
        assertEquals("FAILED",after.get("status")); assertEquals("worker_interrupted",after.get("error_code"));
    }

    @Test void auditViewIsBoundedAndTaskDeletionCascadesReceiptHistory() {
        String workspace=workspace(),task=submit(workspace,"audit"); events.accept(event(task));
        var snapshot=events.status();
        assertEquals(true,snapshot.get("enabled")); assertEquals(100,snapshot.get("recent_limit")); assertEquals(1001,snapshot.get("count_limit"));
        assertTrue(((List<?>)snapshot.get("outbox")).size()<=100); assertTrue(((List<?>)snapshot.get("inbox")).size()<=100);
        db.update("DELETE FROM tasks WHERE id=?",task);
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE task_id=?",Integer.class,task));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM task_event_inbox WHERE task_id=?",Integer.class,task));
    }
}
