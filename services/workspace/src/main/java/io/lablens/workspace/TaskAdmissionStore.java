package io.lablens.workspace;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.TimeUnit;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronization;

/** Database-wide admission serialization; tasks remain the source of capacity truth. */
@Service
public class TaskAdmissionStore {
    private static final String ACTIVE="status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')";
    private final JdbcTemplate db;
    private final MeterRegistry metrics;
    private final int globalLimit;
    private final int workspaceLimit;
    private final AtomicInteger highWater=new AtomicInteger();
    private final AtomicInteger guardInFlight=new AtomicInteger();
    private final Map<String,Timer> guardTimers;
    private final Map<String,Timer> transactionTimers;

    public TaskAdmissionStore(JdbcTemplate db,MeterRegistry metrics,
                              @Value("${lablens.max-pending-global:1000}") int globalLimit,
                              @Value("${lablens.max-pending-tasks}") int workspaceLimit) {
        if (globalLimit<1 || globalLimit>100000 || workspaceLimit<1 || workspaceLimit>100000)
            throw new IllegalArgumentException("Task admission limits must be between 1 and 100000");
        this.db=db; this.metrics=metrics; this.globalLimit=globalLimit; this.workspaceLimit=workspaceLimit;
        this.guardTimers=Map.of("acquired",guardTimer("acquired"),"error",guardTimer("error"));
        this.transactionTimers=Map.of("committed",transactionTimer("committed"),"rolled_back",transactionTimer("rolled_back"),"unknown",transactionTimer("unknown"));
        metrics.gauge("lablens.admission.guard.inflight",guardInFlight);
    }

    private Timer guardTimer(String result) {
        return Timer.builder("lablens.admission.guard.wait").tag("result",result)
            .description("Admission guard statement elapsed time, including database roundtrip and lock wait")
            .register(metrics);
    }
    private Timer transactionTimer(String completion) {
        return Timer.builder("lablens.admission.transaction").tag("completion",completion)
            .description("Submission transaction callback entry to actual transaction completion; excludes connection acquisition")
            .register(metrics);
    }
    /** Registers before the guard, so rejected and outer-rollback attempts remain observable. */
    void observeTransaction() {
        if (!TransactionSynchronizationManager.isActualTransactionActive())
            throw new IllegalStateException("Admission observation requires a submission transaction");
        long started=System.nanoTime();
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCompletion(int status) {
                String completion=switch(status) {
                    case STATUS_COMMITTED -> "committed";
                    case STATUS_ROLLED_BACK -> "rolled_back";
                    default -> "unknown";
                };
                transactionTimers.get(completion).record(System.nanoTime()-started,TimeUnit.NANOSECONDS);
            }
        });
    }

    void lock() {
        if (!TransactionSynchronizationManager.isActualTransactionActive())
            throw new IllegalStateException("Admission guard requires a submission transaction");
        long started=System.nanoTime(); String result="error";
        guardInFlight.incrementAndGet();
        try {
            db.queryForObject("SELECT id FROM task_admission_guard WHERE id=1 FOR UPDATE",Integer.class);
            result="acquired";
        } finally {
            guardInFlight.decrementAndGet();
            guardTimers.get(result).record(System.nanoTime()-started,TimeUnit.NANOSECONDS);
        }
    }
    int globalLimit() { return globalLimit; }
    int workspaceLimit() { return workspaceLimit; }

    /** One consistent read, bounded by the configured cap, using the status covering index. */
    int pendingGlobal(int limit) {
        return db.queryForObject("SELECT COUNT(*) FROM (SELECT id FROM tasks FORCE INDEX(task_dispatch) WHERE "+ACTIVE+" LIMIT ?) AS bounded_active",Integer.class,limit);
    }
    int pendingWorkspace(String workspace) {
        return db.queryForObject("SELECT COUNT(*) FROM (SELECT id FROM tasks FORCE INDEX(task_admission_workspace) WHERE workspace_id=? AND "+ACTIVE+" LIMIT ?) AS bounded_active",Integer.class,workspace,workspaceLimit);
    }
    void accepted(int pending) {
        highWater.accumulateAndGet(pending,Math::max);
        metrics.counter("lablens.admission.outcomes","outcome","accepted","reason","none").increment();
    }
    void reused() { metrics.counter("lablens.admission.outcomes","outcome","reused","reason","none").increment(); }
    void rejected(String reason) { metrics.counter("lablens.admission.outcomes","outcome","rejected","reason",reason).increment(); }

    Map<String,Object> status() {
        int pending=pendingGlobal(globalLimit+1);
        highWater.accumulateAndGet(pending,Math::max);
        List<Long> ages=db.queryForList("SELECT GREATEST(0,TIMESTAMPDIFF(MICROSECOND,created_at,UTC_TIMESTAMP(6)) DIV 1000) FROM tasks FORCE INDEX(task_dispatch) WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1",Long.class);
        Map<String,Object> result=new LinkedHashMap<>();
        result.put("scope","database_all_workspaces");
        result.put("active_statuses",List.of("QUEUED","RUNNING","CANCEL_REQUESTED"));
        result.put("global_pending_limit",globalLimit);
        result.put("workspace_pending_limit",workspaceLimit);
        result.put("pending_global",pending);
        result.put("pending_global_capped",pending==globalLimit+1);
        result.put("oldest_queued_age_ms",ages.isEmpty()?0L:ages.getFirst());
        result.put("observed_pending_high_water",highWater.get());
        result.put("high_water_scope","this_process_successful_submissions_and_status_samples");
        result.put("rate_limit_scope","new_submission_attempts_before_database_admission");
        result.put("configuration_scope","all_instances_sharing_this_database_must_use_the_same_limits");
        return result;
    }
}
