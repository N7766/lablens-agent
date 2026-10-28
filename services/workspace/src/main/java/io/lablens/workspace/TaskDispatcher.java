package io.lablens.workspace;

import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Supplier;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.dao.DeadlockLoserDataAccessException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class TaskDispatcher {
    private static final Logger log=LoggerFactory.getLogger(TaskDispatcher.class);
    private final WorkspaceStore store;
    private final RedisSupport redis;
    private final MeterRegistry metrics;
    private final ThreadPoolExecutor executor;
    private final ThreadPoolExecutor wakeExecutor;
    private final ThreadPoolExecutor cancellationExecutor;
    private final AtomicBoolean cancellationPending=new AtomicBoolean();
    private final AtomicBoolean wakePending=new AtomicBoolean();
    private final Semaphore slots;
    private final HttpClient http;
    private final URI worker;
    private final String workerBase;
    private final int concurrency;
    private final int leaseSeconds;
    private final int timeoutSeconds;
    private final boolean enabled;
    private final AtomicInteger running=new AtomicInteger();
    private final AtomicInteger maxObserved=new AtomicInteger();
    private final String owner=UUID.randomUUID().toString();

    public TaskDispatcher(WorkspaceStore store,RedisSupport redis,MeterRegistry metrics,
                          @Value("${lablens.worker-base-url}") String workerBase,
                          @Value("${lablens.workers}") int concurrency,
                          @Value("${lablens.task-lease-seconds}") int leaseSeconds,
                          @Value("${lablens.worker-timeout-seconds}") int timeoutSeconds,
                          @Value("${lablens.dispatch-enabled}") boolean enabled) {
        if (concurrency<1 || concurrency>32) throw new IllegalArgumentException("lablens.workers must be 1–32");
        if (timeoutSeconds<1 || leaseSeconds<=timeoutSeconds) throw new IllegalArgumentException("Task lease must exceed positive worker timeout");
        this.store=store; this.redis=redis; this.metrics=metrics; this.concurrency=concurrency;
        this.leaseSeconds=leaseSeconds; this.timeoutSeconds=timeoutSeconds; this.enabled=enabled;
        this.workerBase=workerBase.replaceAll("/$","");
        this.worker=URI.create(this.workerBase+"/api/rag/answer");
        this.http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
        this.slots=new Semaphore(concurrency);
        this.executor=new ThreadPoolExecutor(concurrency,concurrency,0,TimeUnit.SECONDS,new ArrayBlockingQueue<>(concurrency),Thread.ofPlatform().name("lablens-worker-",0).factory(),new ThreadPoolExecutor.AbortPolicy());
        this.wakeExecutor=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,new ArrayBlockingQueue<>(1),Thread.ofPlatform().name("lablens-dispatch-",0).factory(),new ThreadPoolExecutor.AbortPolicy());
        this.cancellationExecutor=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,new ArrayBlockingQueue<>(1),Thread.ofPlatform().name("lablens-cancel-",0).factory(),new ThreadPoolExecutor.AbortPolicy());
        metrics.gauge("lablens.workers.active",running);
        metrics.gauge("lablens.workers.max_observed",maxObserved);
        metrics.gauge("lablens.dispatch.pending_wakes",wakeExecutor,e->e.getQueue().size());
    }
    /** Enqueue a bounded wakeup; callers never claim database work on their own thread. */
    public void signal() {
        if (!enabled || wakeExecutor.isShutdown()) return;
        metrics.counter("lablens.dispatch.signals").increment();
        if (!wakePending.compareAndSet(false,true)) {
            metrics.counter("lablens.dispatch.coalesced").increment(); return;
        }
        try {
            wakeExecutor.execute(()->{
                wakePending.set(false);
                metrics.counter("lablens.dispatch.wakeups").increment();
                dispatch();
            });
        } catch (RejectedExecutionException e) { wakePending.set(false); }
    }
    @Scheduled(fixedDelay=250)
    public void poll() { signal(); }
    @Scheduled(fixedDelay=1000)
    public void signalCancellation() {
        if (!enabled || cancellationExecutor.isShutdown() || !cancellationPending.compareAndSet(false,true)) return;
        try { cancellationExecutor.execute(()->{
            try { reconcileCancellations(); }
            catch (RuntimeException e) { metrics.counter("lablens.cancellation.failures").increment(); }
            finally { cancellationPending.set(false); }
        }); } catch (RejectedExecutionException e) { cancellationPending.set(false); }
    }
    /** A missing/unreachable acknowledgement keeps the durable request active. */
    void reconcileCancellations() {
        for (Map<String,Object> task:store.pendingCancellations()) {
            try {
                String id=(String)task.get("id");
                // Only database-issued UUIDs enter this path; models cannot select a URL.
                UUID.fromString(id);
                HttpRequest request=HttpRequest.newBuilder(URI.create(workerBase+"/api/rag/executions/"+id+"/cancel"))
                    .timeout(Duration.ofSeconds(2)).POST(HttpRequest.BodyPublishers.noBody()).build();
                HttpResponse<String> response=http.send(request,HttpResponse.BodyHandlers.ofString());
                if (response.statusCode()!=200 || response.body().length()>20000) continue;
                Map<String,Object> acknowledgement=store.decode(response.body());
                if (!id.equals(acknowledgement.get("task_id")) || !Boolean.TRUE.equals(acknowledgement.get("cancel_requested"))
                        || !Boolean.TRUE.equals(acknowledgement.get("stopped"))) continue;
                Map<String,Object> usage=null;
                if (acknowledgement.get("usage")!=null) {
                    try { usage=WorkerResults.usage(acknowledgement.get("usage")); }
                    catch (IllegalArgumentException ignored) { /* Preserve unknown accounting. */ }
                }
                if (store.acknowledgeCancellation(id,usage)) {
                    metrics.counter("lablens.tasks.cancelled","phase","worker_acknowledged").increment(); signal();
                }
            } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
            catch (Exception e) { metrics.counter("lablens.cancellation.failures").increment(); }
        }
    }
    private void dispatch() {
        if (!enabled || executor.isShutdown()) return;
        try {
            int expired=store.expireLeases();
            if (expired>0) { metrics.counter("lablens.tasks.expired").increment(expired); metrics.counter("lablens.tasks.failed").increment(expired); }
            while (slots.tryAcquire()) {
                WorkspaceStore.Claimed task;
                try { task=withLockRetry("claim",()->store.claim(owner,leaseSeconds)); }
                catch (RuntimeException e) { slots.release(); throw e; }
                if (task==null) { slots.release(); break; }
                try { executor.execute(()->execute(task)); }
                catch (RejectedExecutionException e) {
                    slots.release(); store.fail(task,owner,"worker_unavailable","Local worker executor is shutting down",false,0);
                }
            }
        } catch (RuntimeException e) {
            metrics.counter("lablens.dispatch.failures").increment();
            log.warn("Task dispatch unavailable ({})",e.getClass().getSimpleName());
        }
    }
    private void execute(WorkspaceStore.Claimed task) {
        int active=running.incrementAndGet(); maxObserved.accumulateAndGet(active,Math::max);
        long started=System.nanoTime(); boolean requestSent=false;
        boolean externalPossible=true;
        try {
            if (store.cancellationRequested(task.id())) {
                store.acknowledgeCancellation(task.id(),WorkerResults.zeroUsage(false)); return;
            }
            Map<String,Object> snapshot=store.decode(task.snapshot());
            externalPossible=WorkerResults.canUseProvider(task,snapshot);
            String result=redis.get(task); boolean cached=false;
            Map<String,Object> parsed=null,originUsage=null;
            if (result!=null) {
                try { parsed=store.decode(result); originUsage=WorkerResults.validate(parsed,task,snapshot); cached=true; redis.validatedHit(); }
                catch (RuntimeException e) { redis.invalidate(task); }
            }
            if (!cached) {
                HttpRequest request=HttpRequest.newBuilder(worker).timeout(Duration.ofSeconds(timeoutSeconds))
                    .header("Content-Type","application/json").header("X-LabLens-Task-Id",task.id()).POST(HttpRequest.BodyPublishers.ofString(task.snapshot())).build();
                metrics.counter("lablens.worker.requests").increment(); requestSent=true;
                long workerStart=System.nanoTime();
                HttpResponse<String> response;
                try { response=http.send(request,HttpResponse.BodyHandlers.ofString()); }
                finally { metrics.timer("lablens.worker.duration").record(System.nanoTime()-workerStart,TimeUnit.NANOSECONDS); }
                if (response.statusCode()!=200) {
                    String code=response.statusCode()==503?"rag_unavailable":response.statusCode()==502?"provider_failed":"worker_http_error";
                    String message="RAG worker returned HTTP "+response.statusCode()+". See worker status and logs; the request is not automatically retried.";
                    Map<String,Object> safeError=null;
                    try { if (response.body().length()<=2000000) safeError=WorkerResults.safeError(store.decode(response.body()),task,snapshot); }
                    catch (RuntimeException ignored) { /* Untrusted error details do not override conservative accounting. */ }
                    if (safeError!=null && (((String)safeError.get("status")).startsWith("agent_")
                            || "context_budget_exceeded".equals(safeError.get("status")))) {
                        code=(String)safeError.get("status"); message=(String)safeError.get("error");
                    }
                    @SuppressWarnings("unchecked") Map<String,Object> usage=safeError==null?WorkerResults.uncertainUsage(externalPossible):(Map<String,Object>)safeError.get("usage");
                    finishFailure(task,code,message,usage,safeError,started); return;
                }
                result=response.body();
                if (result.length()>2000000) throw new IllegalStateException("Worker response exceeds result size limit");
                try { parsed=store.decode(result); originUsage=WorkerResults.validate(parsed,task,snapshot); }
                catch (RuntimeException e) {
                    finishFailure(task,"worker_invalid_result","Worker response violated the task identity, status or usage contract. No automatic retry.",externalPossible,started); return;
                }
            }
            Map<String,Object> executionUsage=cached?WorkerResults.zeroUsage(true):originUsage;
            String storedResult=cached?store.encode(WorkerResults.cachedResult(parsed,originUsage)):result;
            String storedUsage=store.encode(executionUsage); boolean cacheHit=cached;
            boolean committed=withLockRetry("finish",()->store.finish(task,owner,storedResult,storedUsage,cacheHit,elapsed(started)));
            if (committed) {
                metrics.counter("lablens.tasks.succeeded").increment();
                if (!cached) redis.put(task,result);
            } else metrics.counter("lablens.tasks.stale_completions").increment();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            finishFailure(task,"worker_interrupted","Worker interrupted; this task is not automatically retried",requestSent && externalPossible,started);
        } catch (Exception e) {
            String code=e instanceof java.net.http.HttpTimeoutException?"worker_timeout":"worker_failed";
            finishFailure(task,code,"RAG worker could not complete the request ("+e.getClass().getSimpleName()+"). No automatic retry.",requestSent && externalPossible,started);
        } finally {
            metrics.timer("lablens.tasks.duration").record(System.nanoTime()-started,TimeUnit.NANOSECONDS);
            running.decrementAndGet(); slots.release(); signal();
            signalCancellation();
        }
    }
    private void finishFailure(WorkspaceStore.Claimed task,String code,String message,boolean unknown,long start) {
        finishFailure(task,code,message,WorkerResults.uncertainUsage(unknown),null,start);
    }
    private void finishFailure(WorkspaceStore.Claimed task,String code,String message,Map<String,Object> usage,Map<String,Object> errorResult,long start) {
        try {
            if ("agent_cancelled".equals(code) && errorResult!=null) store.recordCancellationResult(task,owner,usage,errorResult,elapsed(start));
            if (withLockRetry("fail",()->store.fail(task,owner,code,message,usage,errorResult,elapsed(start)))) metrics.counter("lablens.tasks.failed").increment();
            else metrics.counter("lablens.tasks.stale_completions").increment();
        } catch (RuntimeException e) { metrics.counter("lablens.dispatch.failures").increment(); }
    }
    /** Retry only rollback-safe database lock failures; this closure never includes worker HTTP. */
    private <T> T withLockRetry(String operation,Supplier<T> databaseWrite) {
        for (int attempt=0;;attempt++) {
            try { return databaseWrite.get(); }
            catch (CannotAcquireLockException | DeadlockLoserDataAccessException e) {
                if (attempt>=2 || Thread.currentThread().isInterrupted()) throw e;
                metrics.counter("lablens.database.lock_retries","operation",operation).increment();
                try { Thread.sleep(10L*(attempt+1)); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw e; }
            }
        }
    }
    private long elapsed(long start) { return TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start); }
    public Map<String,Object> status() {
        return Map.of("enabled",enabled,"concurrency",concurrency,"active",running.get(),"max_observed_active",maxObserved.get(),"lease_seconds",leaseSeconds,"timeout_seconds",timeoutSeconds,"queued_tasks_persisted_in","MySQL","recovery_policy","QUEUED survives restart; expired RUNNING fails without automatic model retry; CANCEL_REQUESTED stays active until local worker acknowledgement","dispatch_wake_queue_size",wakeExecutor.getQueue().size(),"dispatch_mode","coalesced submission/completion wakeups plus 250ms recovery poll; cancellation reconciles every second");
    }
    @PreDestroy
    public void shutdown() { wakeExecutor.shutdownNow(); cancellationExecutor.shutdownNow(); executor.shutdownNow(); }
}
