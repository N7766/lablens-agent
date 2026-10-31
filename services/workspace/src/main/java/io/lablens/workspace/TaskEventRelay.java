package io.lablens.workspace;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

/** One bounded publisher; uncertain delivery is replayable because inbox consumption is idempotent. */
public final class TaskEventRelay {
    private final TaskEventStore store;
    private final RabbitTemplate rabbit;
    private final ObjectMapper mapper;
    private final MeterRegistry metrics;
    private final String exchange,routingKey;
    private final int confirmTimeoutMs,leaseSeconds,maxAttempts,batchSize;
    private final String owner=UUID.randomUUID().toString();
    private final AtomicBoolean pending=new AtomicBoolean();
    private final ThreadPoolExecutor executor=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,
        new ArrayBlockingQueue<>(1),Thread.ofPlatform().name("lablens-outbox-",0).factory(),new ThreadPoolExecutor.AbortPolicy());

    public TaskEventRelay(TaskEventStore store,RabbitTemplate rabbit,ObjectMapper mapper,MeterRegistry metrics,
                          String exchange,String routingKey,int confirmTimeoutMs,int leaseSeconds,int maxAttempts,int batchSize) {
        if (confirmTimeoutMs<1 || confirmTimeoutMs>10000 || leaseSeconds<5 || leaseSeconds>300
                || leaseSeconds*1000<=confirmTimeoutMs+3000 || maxAttempts<1 || maxAttempts>20 || batchSize<1 || batchSize>100)
            throw new IllegalArgumentException("Invalid bounded outbox relay limits");
        this.store=store; this.rabbit=rabbit; this.mapper=mapper; this.metrics=metrics;
        this.exchange=exchange; this.routingKey=routingKey; this.confirmTimeoutMs=confirmTimeoutMs;
        this.leaseSeconds=leaseSeconds; this.maxAttempts=maxAttempts; this.batchSize=batchSize;
        metrics.gauge("lablens.mq.relay.pending",executor,e->e.getQueue().size());
    }

    /** Scheduling never waits for broker confirms on the shared application scheduler. */
    public void signal() {
        if (executor.isShutdown() || !pending.compareAndSet(false,true)) return;
        try { executor.execute(()->{
            try { relayBatch(); }
            catch (RuntimeException e) { metrics.counter("lablens.mq.relay.failures").increment(); }
            finally { pending.set(false); }
        }); } catch (RejectedExecutionException e) { pending.set(false); }
    }

    /** Package-visible deterministic step used by integration tests, with no scheduler or retry hook. */
    synchronized void relayBatch() {
        for (int i=0;i<batchSize && !Thread.currentThread().isInterrupted();i++) {
            var next=store.claim(owner,leaseSeconds,maxAttempts);
            if (next.isEmpty()) return;
            var claimed=next.get();
            String failure=null;
            try {
                byte[] body=mapper.writeValueAsBytes(claimed.event());
                if (body.length>TaskEventConsumer.MAX_BODY_BYTES) throw new IllegalArgumentException("Event body exceeds bound");
                var props=new MessageProperties();
                props.setContentType(MessageProperties.CONTENT_TYPE_JSON);
                props.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
                props.setMessageId(claimed.event().eventId());
                props.setType(TaskEventStore.EVENT_TYPE);
                var correlation=new CorrelationData(claimed.event().eventId()+":"+claimed.leaseToken());
                metrics.counter("lablens.mq.publish.attempts").increment();
                rabbit.send(exchange,routingKey,new Message(body,props),correlation);
                var confirm=correlation.getFuture().get(confirmTimeoutMs,TimeUnit.MILLISECONDS);
                if (!confirm.isAck()) failure="broker_nack";
                else if (correlation.getReturned()!=null) failure="unroutable";
                else {
                    // The future completes after returned metadata is set. DB lease token fences stale confirms.
                    if (store.published(claimed,owner)) metrics.counter("lablens.mq.publish.confirmed").increment();
                    else metrics.counter("lablens.mq.publish.stale_confirms").increment();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt(); failure="interrupted_delivery_unknown";
            } catch (TimeoutException e) { failure="confirm_timeout_delivery_unknown"; }
            catch (Exception e) { failure="publish_or_record_failed"; }
            if (failure!=null) {
                metrics.counter("lablens.mq.publish.failures","reason",failure).increment();
                // Retry only the notification. No worker or LLM request is issued here.
                store.retry(claimed,owner,failure,maxAttempts);
            }
        }
    }

    @PreDestroy public void shutdown() { executor.shutdownNow(); }
}
