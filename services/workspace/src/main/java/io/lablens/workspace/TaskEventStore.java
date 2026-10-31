package io.lablens.workspace;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Durable notifications only. Task ownership and execution remain in WorkspaceStore. */
@Service
public class TaskEventStore {
    public static final String EVENT_TYPE="task.available.v1";
    public static final int SCHEMA_VERSION=1;
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final boolean enabled;
    public record Event(String eventId,String taskId,String workspaceId,String snapshotRef,int schemaVersion,String eventType) {}
    public record Claimed(Event event,String leaseToken,int attempts) {}
    public enum Acceptance { ACCEPTED, DUPLICATE, NOT_QUEUED }

    public TaskEventStore(JdbcTemplate db,TransactionTemplate transaction,
                          @Value("${lablens.mq.enabled:false}") boolean enabled) {
        this.db=db; this.enabled=enabled;
        this.tx=new TransactionTemplate(Objects.requireNonNull(transaction.getTransactionManager()));
        this.tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    /** Called after task insertion, inside the very same submission transaction. */
    void enqueue(String taskId,String workspaceId,long revision) {
        if (!enabled) return;
        if (!TransactionSynchronizationManager.isActualTransactionActive())
            throw new IllegalStateException("Task and notification must share a transaction");
        db.update("INSERT INTO task_event_outbox(event_id,task_id,workspace_id,snapshot_ref,schema_version,event_type) VALUES (?,?,?,?,?,?)",
            UUID.randomUUID().toString(),taskId,workspaceId,workspaceId+":"+revision,SCHEMA_VERSION,EVENT_TYPE);
    }

    /** A short locking transaction; never hold the connection during a broker call. */
    public Optional<Claimed> claim(String owner,int leaseSeconds,int maxAttempts) {
        validateOwner(owner); bounded(leaseSeconds,1,300,"leaseSeconds"); bounded(maxAttempts,1,20,"maxAttempts");
        return tx.execute(status->{
            // next_attempt_at is the retry time, or lease expiry while SENDING. Terminal
            // rows have NULL here, so the ordered index skips published history entirely.
            // Bounded cleanup lets abandoned final attempts become visible DEAD records.
            for (int scanned=0;scanned<100;scanned++) {
                List<Map<String,Object>> rows=db.query("SELECT * FROM task_event_outbox FORCE INDEX (outbox_dispatch) "
                    +"WHERE next_attempt_at<=UTC_TIMESTAMP(6) AND (state IN ('PENDING','RETRY') OR (state='SENDING' AND lease_until<=UTC_TIMESTAMP(6))) "
                    +"ORDER BY next_attempt_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED",this::outboxRow);
                if (rows.isEmpty()) return Optional.empty();
                Map<String,Object> row=rows.getFirst();
                Event event=event(row);
                int attempts=((Number)row.get("attempts")).intValue();
                if (attempts>=maxAttempts) {
                    db.update("UPDATE task_event_outbox SET state='DEAD',owner=NULL,lease_token=NULL,lease_until=NULL,next_attempt_at=NULL,last_error='attempts_exhausted',updated_at=UTC_TIMESTAMP(6) WHERE event_id=?",event.eventId());
                    continue;
                }
                String token=UUID.randomUUID().toString();
                db.update("UPDATE task_event_outbox SET state='SENDING',attempts=attempts+1,owner=?,lease_token=?,lease_until=TIMESTAMPADD(SECOND,?,UTC_TIMESTAMP(6)),next_attempt_at=TIMESTAMPADD(SECOND,?,UTC_TIMESTAMP(6)),updated_at=UTC_TIMESTAMP(6) WHERE event_id=?",
                    owner,token,leaseSeconds,leaseSeconds,event.eventId());
                return Optional.of(new Claimed(event,token,attempts+1));
            }
            return Optional.empty();
        });
    }

    /** A broker confirm is necessary but cannot make an expired publisher authoritative. */
    public boolean published(Claimed claimed,String owner) {
        validateOwner(owner); Objects.requireNonNull(claimed);
        return db.update("UPDATE task_event_outbox SET state='PUBLISHED',published_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6),owner=NULL,lease_token=NULL,lease_until=NULL,next_attempt_at=NULL,last_error=NULL "
            +"WHERE event_id=? AND state='SENDING' AND owner=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(6)",
            claimed.event().eventId(),owner,claimed.leaseToken())==1;
    }

    /** Unknown confirms are retried at least once; the inbox absorbs any duplicate delivery. */
    public boolean retry(Claimed claimed,String owner,String safeError,int maxAttempts) {
        validateOwner(owner); bounded(maxAttempts,1,20,"maxAttempts"); Objects.requireNonNull(claimed);
        String code=safeError!=null && safeError.matches("[a-z][a-z0-9_]{0,63}")?safeError:"publish_failed";
        return db.update("UPDATE task_event_outbox SET state=IF(attempts>=?,'DEAD','RETRY'),"
            +"next_attempt_at=IF(attempts>=?,NULL,TIMESTAMPADD(SECOND,LEAST(60,POW(2,LEAST(attempts,6))),UTC_TIMESTAMP(6))),"
            +"owner=NULL,lease_token=NULL,lease_until=NULL,last_error=?,updated_at=UTC_TIMESTAMP(6) "
            +"WHERE event_id=? AND state='SENDING' AND owner=? AND lease_token=? AND lease_until>UTC_TIMESTAMP(6)",
            maxAttempts,maxAttempts,code,claimed.event().eventId(),owner,claimed.leaseToken())==1;
    }

    /**
     * Persist receipt before ACK. Validation runs even on duplicate IDs. This method never
     * resets a task: RUNNING, FAILED, cancelled and usage-unknown tasks stay untouched.
     * Polling remains the recovery path if the process dies after this commit and before signal().
     */
    public Acceptance accept(Event incoming) {
        validateEvent(incoming);
        return tx.execute(status->{
            List<Map<String,Object>> rows=db.query("SELECT o.*,t.workspace_id AS actual_workspace_id,t.snapshot_revision AS actual_revision,t.status AS task_status "
                +"FROM task_event_outbox o JOIN tasks t ON t.id=o.task_id WHERE o.event_id=? FOR UPDATE",(rs,n)->{
                    Map<String,Object> row=outboxRow(rs,n);
                    row.put("actual_workspace_id",rs.getString("actual_workspace_id"));
                    row.put("actual_revision",rs.getLong("actual_revision"));
                    row.put("task_status",rs.getString("task_status")); return row;
                },incoming.eventId());
            if (rows.isEmpty()) throw new IllegalArgumentException("Unknown task event");
            Map<String,Object> row=rows.getFirst();
            if (!incoming.equals(event(row)) || !incoming.workspaceId().equals(row.get("actual_workspace_id"))
                    || !incoming.snapshotRef().equals(row.get("actual_workspace_id")+":"+row.get("actual_revision")))
                throw new IllegalArgumentException("Task event identity mismatch");
            if (Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM task_event_inbox WHERE event_id=?)",Boolean.class,incoming.eventId())))
                return Acceptance.DUPLICATE;
            String taskStatus=(String)row.get("task_status");
            Acceptance accepted="QUEUED".equals(taskStatus)?Acceptance.ACCEPTED:Acceptance.NOT_QUEUED;
            db.update("INSERT INTO task_event_inbox(event_id,task_id,disposition,task_status) VALUES (?,?,?,?)",incoming.eventId(),incoming.taskId(),accepted.name(),taskStatus);
            return accepted;
        });
    }

    /** Bounded operational view. Counts explicitly saturate at 1,001; never full-scan history. */
    public Map<String,Object> status() {
        Map<String,Object> counts=new LinkedHashMap<>();
        for (String state:List.of("PENDING","SENDING","RETRY","PUBLISHED","DEAD")) {
            Integer count=db.queryForObject("SELECT COUNT(*) FROM (SELECT event_id FROM task_event_outbox WHERE state=? LIMIT 1001) limited",Integer.class,state);
            counts.put(state,Map.of("count",count,"capped",count==1001));
        }
        Map<String,Object> inboxCounts=new LinkedHashMap<>();
        for (String disposition:List.of("ACCEPTED","NOT_QUEUED")) {
            Integer count=db.queryForObject("SELECT COUNT(*) FROM (SELECT event_id FROM task_event_inbox WHERE disposition=? LIMIT 1001) limited",Integer.class,disposition);
            inboxCounts.put(disposition,Map.of("count",count,"capped",count==1001));
        }
        long oldestAge=0;
        for (String state:List.of("PENDING","SENDING","RETRY")) {
            List<Long> ages=db.queryForList("SELECT TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP(6)) FROM task_event_outbox WHERE state=? ORDER BY created_at LIMIT 1",Long.class,state);
            if (!ages.isEmpty()) oldestAge=Math.max(oldestAge,ages.getFirst());
        }
        List<Map<String,Object>> recent=db.query("SELECT * FROM task_event_outbox ORDER BY created_at DESC,event_id DESC LIMIT 100",this::outboxRow);
        List<Map<String,Object>> inbox=db.query("SELECT event_id,task_id,disposition,task_status,accepted_at FROM task_event_inbox ORDER BY accepted_at DESC,event_id DESC LIMIT 100",(rs,n)->{
            Map<String,Object> row=new LinkedHashMap<>();
            for (String field:List.of("event_id","task_id","disposition","task_status")) row.put(field,rs.getString(field));
            row.put("accepted_at",rs.getTimestamp("accepted_at").toInstant().toString()); return row;
        });
        return Map.of("enabled",enabled,"broker_connection","not_checked","outbox_counts",counts,"inbox_counts",inboxCounts,"oldest_pending_age_seconds",oldestAge,"count_limit",1001,"recent_limit",100,"outbox",recent,"inbox",inbox);
    }

    private Map<String,Object> outboxRow(ResultSet rs,int ignored) throws SQLException {
        Map<String,Object> row=new LinkedHashMap<>();
        for (String field:List.of("event_id","task_id","workspace_id","snapshot_ref","event_type","state","owner","lease_token","last_error")) row.put(field,rs.getString(field));
        row.put("schema_version",rs.getInt("schema_version")); row.put("attempts",rs.getInt("attempts"));
        for (String field:List.of("lease_until","next_attempt_at","created_at","updated_at","published_at")) {
            var timestamp=rs.getTimestamp(field); row.put(field,timestamp==null?null:timestamp.toInstant().toString());
        }
        return row;
    }
    private static Event event(Map<String,Object> row) {
        return new Event((String)row.get("event_id"),(String)row.get("task_id"),(String)row.get("workspace_id"),(String)row.get("snapshot_ref"),((Number)row.get("schema_version")).intValue(),(String)row.get("event_type"));
    }
    private static void validateEvent(Event event) {
        if (event==null || !uuid(event.eventId()) || !uuid(event.taskId()) || !uuid(event.workspaceId())
                || event.snapshotRef()==null || !event.snapshotRef().matches(java.util.regex.Pattern.quote(event.workspaceId())+":[0-9]{1,19}")
                || event.schemaVersion()!=SCHEMA_VERSION || !EVENT_TYPE.equals(event.eventType()))
            throw new IllegalArgumentException("Unsupported task event envelope");
    }
    private static boolean uuid(String value) {
        return value!=null && value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
    }
    private static void validateOwner(String owner) {
        if (owner==null || owner.isBlank() || owner.length()>100) throw new IllegalArgumentException("Invalid publisher owner");
    }
    private static void bounded(int value,int min,int max,String field) {
        if (value<min || value>max) throw new IllegalArgumentException("Invalid "+field);
    }
}
