package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.Optional;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

class TaskEventRelayTest {
    private final TaskEventStore store=mock(TaskEventStore.class);
    private final RabbitTemplate rabbit=mock(RabbitTemplate.class);
    private final SimpleMeterRegistry metrics=new SimpleMeterRegistry();
    private final TaskEventStore.Claimed claimed=new TaskEventStore.Claimed(new TaskEventStore.Event(
        "event","task","workspace","workspace:1",1,TaskEventStore.EVENT_TYPE),"lease",1);
    private TaskEventRelay relay(int timeout,int batch) {
        when(store.claim(anyString(),anyInt(),anyInt())).thenReturn(Optional.of(claimed),Optional.empty());
        when(store.published(eq(claimed),anyString())).thenReturn(true);
        return new TaskEventRelay(store,rabbit,new ObjectMapper(),metrics,"events","task.available.v1",timeout,15,5,batch);
    }
    @Test void marksPublishedOnlyAfterAckWithNoReturnAndSendsPersistentReferenceOnlyBody() throws Exception {
        var relay=relay(100,1);
        doAnswer(call->{
            var message=(Message)call.getArgument(2);
            assertEquals(MessageDeliveryMode.PERSISTENT,message.getMessageProperties().getDeliveryMode());
            assertEquals("event",message.getMessageProperties().getMessageId());
            assertEquals(claimed.event(),new ObjectMapper().readValue(message.getBody(),TaskEventStore.Event.class));
            verify(store,never()).published(any(),anyString());
            ((CorrelationData)call.getArgument(3)).getFuture().complete(new CorrelationData.Confirm(true,null));
            return null;
        }).when(rabbit).send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try { relay.relayBatch(); verify(store).published(eq(claimed),anyString()); verify(store,never()).retry(any(),anyString(),anyString(),anyInt()); }
        finally { relay.shutdown(); }
    }
    @Test void returnedMessageCannotBePublishedEvenWhenBrokerAcknowledges() {
        var relay=relay(100,1);
        doAnswer(call->{
            var correlation=(CorrelationData)call.getArgument(3);
            correlation.setReturned(new ReturnedMessage(call.getArgument(2),312,"NO_ROUTE","events","missing"));
            correlation.getFuture().complete(new CorrelationData.Confirm(true,null)); return null;
        }).when(rabbit).send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try { relay.relayBatch(); verify(store,never()).published(any(),anyString()); verify(store).retry(eq(claimed),anyString(),eq("unroutable"),eq(5)); }
        finally { relay.shutdown(); }
    }
    @Test void brokerNackRetainsNotificationForBoundedDatabaseRetry() {
        var relay=relay(100,1);
        doAnswer(call->{ ((CorrelationData)call.getArgument(3)).getFuture().complete(new CorrelationData.Confirm(false,"reason")); return null; })
            .when(rabbit).send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try { relay.relayBatch(); verify(store,never()).published(any(),anyString()); verify(store).retry(eq(claimed),anyString(),eq("broker_nack"),eq(5)); }
        finally { relay.shutdown(); }
    }
    @Test void missingConfirmIsUnknownAndLateAckCannotPublish() {
        var relay=relay(2,1); var correlation=new CorrelationData[1];
        doAnswer(call->{ correlation[0]=call.getArgument(3); return null; })
            .when(rabbit).send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try {
            relay.relayBatch(); correlation[0].getFuture().complete(new CorrelationData.Confirm(true,null));
            verify(store,never()).published(any(),anyString());
            verify(store).retry(eq(claimed),anyString(),eq("confirm_timeout_delivery_unknown"),eq(5));
        } finally { relay.shutdown(); }
    }
    @Test void connectionFailureRetriesOnlyNotificationAndStoresNoExceptionText() {
        var relay=relay(100,1);
        doThrow(new AmqpException("private URI or credentials must not persist")).when(rabbit)
            .send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try { relay.relayBatch(); verify(store,never()).published(any(),anyString()); verify(store).retry(eq(claimed),anyString(),eq("publish_or_record_failed"),eq(5)); }
        finally { relay.shutdown(); }
    }
    @Test void staleLeaseConfirmDoesNotOverwriteAnotherPublisher() {
        var relay=relay(100,1); when(store.published(eq(claimed),anyString())).thenReturn(false);
        doAnswer(call->{ ((CorrelationData)call.getArgument(3)).getFuture().complete(new CorrelationData.Confirm(true,null)); return null; })
            .when(rabbit).send(anyString(),anyString(),any(Message.class),any(CorrelationData.class));
        try { relay.relayBatch(); assertEquals(1,metrics.get("lablens.mq.publish.stale_confirms").counter().count()); verify(store,never()).retry(any(),anyString(),anyString(),anyInt()); }
        finally { relay.shutdown(); }
    }
    @Test void repeatedSignalsCannotGrowWorkOrRunInlineWhileDatabaseIsBlocked() throws Exception {
        var relay=relay(100,1); var entered=new CountDownLatch(1); var release=new CountDownLatch(1);
        when(store.claim(anyString(),anyInt(),anyInt())).thenAnswer(call->{ entered.countDown(); release.await(3,TimeUnit.SECONDS); return Optional.empty(); });
        try {
            relay.signal(); assertTrue(entered.await(1,TimeUnit.SECONDS));
            for (int i=0;i<1000;i++) relay.signal();
            verify(store,times(1)).claim(anyString(),anyInt(),anyInt());
            assertEquals(0,metrics.get("lablens.mq.relay.pending").gauge().value());
        } finally { release.countDown(); relay.shutdown(); }
    }
}
