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
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import org.springframework.dao.CannotAcquireLockException;
import org.junit.jupiter.api.Test;

class TaskDispatcherTest {
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
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/executions/"+id+"/cancel",exchange->{
            calls.incrementAndGet(); byte[] response=mapper.writeValueAsBytes(replies.remove());
            exchange.sendResponseHeaders(200,response.length);
            exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:"+server.getAddress().getPort(),1,10,5,false);
        try {
            dispatcher.reconcileCancellations(); dispatcher.reconcileCancellations();
            verify(store,never()).acknowledgeCancellation(anyString(),any());
            dispatcher.reconcileCancellations();
            verify(store).acknowledgeCancellation(eq(id),isNull());
            assertEquals(3,calls.get());
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
            assertEquals(999,metrics.get("lablens.dispatch.coalesced").counter().count());
            verify(store,times(1)).claim(anyString(),anyInt());
        } finally { release.countDown(); dispatcher.shutdown(); }
    }
    @Test void retryableCompletionLockFailureRetriesOnlyDatabaseWriteNotWorkerHttp() throws Exception {
        var store=mock(WorkspaceStore.class); var redis=mock(RedisSupport.class); var metrics=new SimpleMeterRegistry();
        var mapper=new ObjectMapper(); var httpCalls=new AtomicInteger();
        var task=new WorkspaceStore.Claimed("task","workspace",1,"hash","extractive","{\"strategy\":\"bm25\"}");
        when(store.claim(anyString(),anyInt())).thenReturn(task,null);
        when(store.decode(anyString())).thenAnswer(call->mapper.readValue((String)call.getArgument(0),new TypeReference<Map<String,Object>>() {}));
        when(store.encode(any())).thenAnswer(call->mapper.writeValueAsString(call.getArgument(0)));
        byte[] response=mapper.writeValueAsBytes(Map.of("status","excerpts","mode","extractive","retrieval",Map.of("snapshot_id","workspace:1","strategy","bm25"),"usage",WorkerResults.zeroUsage(false)));
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/answer",exchange->{
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
        try {
            dispatcher.signal(); assertTrue(finished.await(5,TimeUnit.SECONDS));
            assertEquals(1,httpCalls.get());
            verify(store,times(2)).finish(any(),anyString(),anyString(),anyString(),anyBoolean(),anyLong());
            verify(store,never()).fail(any(),anyString(),anyString(),anyString(),anyMap(),any(),anyLong());
            assertEquals(1,metrics.get("lablens.database.lock_retries").tag("operation","finish").counter().count());
        } finally { dispatcher.shutdown(); server.stop(0); }
    }
}
