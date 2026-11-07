package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import org.springframework.dao.CannotAcquireLockException;
import org.junit.jupiter.api.Test;

class TaskDispatcherTest {
    private static final String SERVICE_KEY="round16-http-fixture-key-123456789";
    @Test void fixedQueueBudgetSurvivesAnOccupancyChangeBetweenGaugeReads() {
        var metrics=new SimpleMeterRegistry(); var takeAfterSize=new AtomicBoolean();
        // Deterministically model a worker taking a queued item immediately after size() samples it.
        var queue=new ArrayBlockingQueue<Runnable>(1) {
            @Override public int size() {
                int sampled=super.size();
                if (takeAfterSize.compareAndSet(true,false)) super.poll();
                return sampled;
            }
        };
        var executor=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,queue);
        try {
            TaskDispatcher.observeExecutor(metrics,"controlled",executor,1);
            var capacity=metrics.get("lablens.executor.queue.capacity").tag("executor","controlled").gauge();
            var occupied=metrics.get("lablens.executor.queued").tag("executor","controlled").gauge();
            assertEquals(1,capacity.value());
            for (int i=0;i<10;i++) {
                assertTrue(queue.offer(()->{})); takeAfterSize.set(true);
                // A budget scrape must remain valid while the next occupancy scrape releases the item.
                assertEquals(1,capacity.value());
                assertEquals(1,occupied.value());
                assertEquals(1,queue.remainingCapacity());
                assertEquals(1,capacity.value());
            }
        } finally { executor.shutdownNow(); metrics.close(); }
    }
    @Test void cancellationReconciliationRequiresStoppedIdentityAndRetainsUnknownUsageWhenWorkerLostState() throws Exception {
        var store=mock(WorkspaceStore.class); var redis=mock(RedisSupport.class); var metrics=new SimpleMeterRegistry();
        var mapper=new ObjectMapper(); String id=java.util.UUID.randomUUID().toString();
        when(store.decode(anyString())).thenAnswer(call->mapper.readValue((String)call.getArgument(0),new TypeReference<Map<String,Object>>() {}));
        when(store.pendingCancellations()).thenReturn(java.util.List.of(Map.of("id",id)));
        var replies=new ConcurrentLinkedQueue<Map<String,Object>>();
        replies.add(Map.of("task_id",id,"cancel_requested",true,"stopped",false));
        replies.add(Map.of("task_id","wrong-task","cancel_requested",true,"stopped",true));
        replies.add(Map.of("task_id",id,"cancel_requested",true,"stopped",true));
        var calls=new AtomicInteger();
        var serviceHeaders=new ConcurrentLinkedQueue<String>();
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/executions/"+id+"/cancel",exchange->{
            serviceHeaders.add(String.valueOf(exchange.getRequestHeaders().getFirst("X-LabLens-Service-Key")));
            calls.incrementAndGet(); byte[] response=mapper.writeValueAsBytes(replies.remove());
            exchange.sendResponseHeaders(200,response.length);
            exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:"+server.getAddress().getPort(),1,10,5,false);
        org.springframework.test.util.ReflectionTestUtils.setField(dispatcher,"serviceKey",SERVICE_KEY);
        try {
            dispatcher.reconcileCancellations(); dispatcher.reconcileCancellations();
            verify(store,never()).acknowledgeCancellation(anyString(),any());
            dispatcher.reconcileCancellations();
            verify(store).acknowledgeCancellation(eq(id),isNull());
            assertEquals(3,calls.get());
            assertEquals(java.util.List.of(SERVICE_KEY,SERVICE_KEY,SERVICE_KEY),new java.util.ArrayList<>(serviceHeaders));
            server.stop(0); dispatcher.reconcileCancellations();
            verify(store,times(1)).acknowledgeCancellation(anyString(),any());
        } finally { dispatcher.shutdown(); server.stop(0); }
    }
    @Test void singleSubmissionWakeDrainsQueuedTasksOnCompletionWithoutAPeriodicPoll() throws Exception {
        var store=mock(WorkspaceStore.class); var redis=mock(RedisSupport.class); var metrics=new SimpleMeterRegistry();
        var mapper=new ObjectMapper();
        when(store.decode(anyString())).thenAnswer(call->mapper.readValue((String)call.getArgument(0),new TypeReference<Map<String,Object>>() {}));
        when(store.encode(any())).thenAnswer(call->mapper.writeValueAsString(call.getArgument(0)));
        var queue=new ConcurrentLinkedQueue<WorkspaceStore.Claimed>();
        for (int i=0;i<6;i++) queue.add(new WorkspaceStore.Claimed("task"+i,"workspace",1,"hash"+i,"extractive","{\"strategy\":\"bm25\"}"));
        when(store.claim(anyString(),anyInt())).thenAnswer(call->queue.poll());
        var firstTwo=new CountDownLatch(2); var release=new CountDownLatch(1); var finished=new CountDownLatch(6);
        var active=new AtomicInteger(); var maximum=new AtomicInteger();
        String cached=mapper.writeValueAsString(Map.of("status","excerpts","mode","extractive","retrieval",Map.of("snapshot_id","workspace:1","strategy","bm25"),"usage",WorkerResults.zeroUsage(false)));
        when(redis.get(any())).thenAnswer(call->{
            maximum.accumulateAndGet(active.incrementAndGet(),Math::max); firstTwo.countDown();
            try { if (!release.await(5,TimeUnit.SECONDS)) throw new IllegalStateException("Test did not release workers"); return cached; }
            finally { active.decrementAndGet(); }
        });
        when(store.finish(any(),anyString(),anyString(),anyString(),anyBoolean(),anyLong())).thenAnswer(call->{finished.countDown(); return true;});
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:1",2,10,5,true);
        try {
            // This object is constructed directly, so Spring's periodic scheduler never runs.
            dispatcher.signal();
            assertTrue(firstTwo.await(3,TimeUnit.SECONDS));
            assertEquals(4,queue.size()); assertEquals(2,maximum.get());
            assertEquals(2,metrics.get("lablens.executor.active").tag("executor","worker").gauge().value());
            assertEquals(2,metrics.get("lablens.executor.pool.max").tag("executor","worker").gauge().value());
            assertEquals(0,metrics.get("lablens.executor.queued").tag("executor","worker").gauge().value());
            assertEquals(2,metrics.get("lablens.executor.queue.capacity").tag("executor","worker").gauge().value());
            release.countDown();
            assertTrue(finished.await(5,TimeUnit.SECONDS),"Completion signals must drain the remaining four tasks without a poll");
            assertEquals(0,queue.size()); assertEquals(2,maximum.get());
            assertEquals(2,dispatcher.status().get("max_observed_active"));
            verify(store,times(6)).finish(any(),anyString(),anyString(),anyString(),anyBoolean(),anyLong());
            assertNull(metrics.find("lablens.worker.requests").counter(),"Controlled cached tasks must not contact a provider");
        } finally { release.countDown(); dispatcher.shutdown(); }
    }
    @Test void blockedDatabaseDoesNotBlockSignalsOrCreateAnUnboundedWakeQueue() throws Exception {
        var store=mock(WorkspaceStore.class); var redis=mock(RedisSupport.class); var metrics=new SimpleMeterRegistry();
        var entered=new CountDownLatch(1); var release=new CountDownLatch(1);
        when(store.claim(anyString(),anyInt())).thenAnswer(call->{entered.countDown(); release.await(5,TimeUnit.SECONDS); return null;});
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:1",2,10,5,true);
        try {
            dispatcher.signal(); assertTrue(entered.await(3,TimeUnit.SECONDS));
            // Database claim remains blocked throughout all signals. There is no inline DB work.
            for (int i=0;i<1000;i++) dispatcher.signal();
            assertEquals(1,dispatcher.status().get("dispatch_wake_queue_size"));
            assertEquals(1,metrics.get("lablens.executor.active").tag("executor","dispatch").gauge().value());
            assertEquals(1,metrics.get("lablens.executor.queued").tag("executor","dispatch").gauge().value());
            assertEquals(1,metrics.get("lablens.executor.queue.capacity").tag("executor","dispatch").gauge().value());
            assertEquals(999,metrics.get("lablens.dispatch.coalesced").counter().count());
            verify(store,times(1)).claim(anyString(),anyInt());
        } finally { release.countDown(); dispatcher.shutdown(); }
    }
    @Test void retryableCompletionLockFailureRetriesOnlyDatabaseWriteNotWorkerHttp() throws Exception {
        var store=mock(WorkspaceStore.class); var redis=mock(RedisSupport.class); var metrics=new SimpleMeterRegistry();
        var mapper=new ObjectMapper(); var httpCalls=new AtomicInteger();
        var serviceHeader=new java.util.concurrent.atomic.AtomicReference<String>();
        var task=new WorkspaceStore.Claimed("task","workspace",1,"hash","extractive","{\"strategy\":\"bm25\"}");
        when(store.claim(anyString(),anyInt())).thenReturn(task,null);
        when(store.decode(anyString())).thenAnswer(call->mapper.readValue((String)call.getArgument(0),new TypeReference<Map<String,Object>>() {}));
        when(store.encode(any())).thenAnswer(call->mapper.writeValueAsString(call.getArgument(0)));
        byte[] response=mapper.writeValueAsBytes(Map.of("status","excerpts","mode","extractive","retrieval",Map.of("snapshot_id","workspace:1","strategy","bm25"),"usage",WorkerResults.zeroUsage(false)));
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/answer",exchange->{
            serviceHeader.set(exchange.getRequestHeaders().getFirst("X-LabLens-Service-Key"));
            httpCalls.incrementAndGet(); exchange.getRequestBody().readAllBytes();
            exchange.getResponseHeaders().add("Content-Type","application/json");
            exchange.sendResponseHeaders(200,response.length); exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        var finished=new CountDownLatch(1);
        when(store.finish(any(),anyString(),anyString(),anyString(),anyBoolean(),anyLong()))
            .thenThrow(new CannotAcquireLockException("Injected rollback-safe deadlock"))
            .thenAnswer(call->{finished.countDown(); return true;});
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:"+server.getAddress().getPort(),2,10,5,true);
        org.springframework.test.util.ReflectionTestUtils.setField(dispatcher,"serviceKey",SERVICE_KEY);
        try {
            dispatcher.signal(); assertTrue(finished.await(5,TimeUnit.SECONDS));
            assertEquals(1,httpCalls.get());
            assertEquals(SERVICE_KEY,serviceHeader.get());
            verify(store,times(2)).finish(any(),anyString(),anyString(),anyString(),anyBoolean(),anyLong());
            verify(store,never()).fail(any(),anyString(),anyString(),anyString(),anyMap(),any(),anyLong());
            assertEquals(1,metrics.get("lablens.database.lock_retries").tag("operation","finish").counter().count());
        } finally { dispatcher.shutdown(); server.stop(0); }
    }
}
