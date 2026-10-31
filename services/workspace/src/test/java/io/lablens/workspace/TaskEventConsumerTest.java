package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.dao.DataAccessResourceFailureException;

class TaskEventConsumerTest {
    private final TaskEventStore store=mock(TaskEventStore.class);
    private final TaskDispatcher dispatcher=mock(TaskDispatcher.class);
    private final Channel channel=mock(Channel.class);
    private final ObjectMapper mapper=new ObjectMapper();
    private final SimpleMeterRegistry metrics=new SimpleMeterRegistry();
    private final TaskEventStore.Event event=new TaskEventStore.Event("event","task","workspace","workspace:1",1,TaskEventStore.EVENT_TYPE);
    private final TaskEventConsumer consumer=new TaskEventConsumer(store,dispatcher,mapper,metrics,3,1);
    private Message message(byte[] bytes) {
        var props=new MessageProperties(); props.setDeliveryTag(7); props.setContentType(MessageProperties.CONTENT_TYPE_JSON); props.setMessageId("event");
        return new Message(bytes,props);
    }
    private Message valid() throws Exception { return message(mapper.writeValueAsBytes(event)); }
    @Test void acknowledgementFollowsCommittedInboxAndWakeup() throws Exception {
        when(store.accept(event)).thenReturn(TaskEventStore.Acceptance.ACCEPTED);
        consumer.onMessage(valid(),channel);
        var order=inOrder(store,dispatcher,channel); order.verify(store).accept(event); order.verify(dispatcher).signal(); order.verify(channel).basicAck(7,false);
        verify(channel,never()).basicReject(anyLong(),anyBoolean());
    }
    @Test void duplicateAndNonQueuedReceiptsNeverWakeOrExecuteTasks() throws Exception {
        when(store.accept(event)).thenReturn(TaskEventStore.Acceptance.DUPLICATE,TaskEventStore.Acceptance.NOT_QUEUED);
        consumer.onMessage(valid(),channel); consumer.onMessage(valid(),channel);
        verify(channel,times(2)).basicAck(7,false); verifyNoInteractions(dispatcher);
    }
    @Test void ackLossAfterCommitCanBeRedeliveredWithoutSecondWakeup() throws Exception {
        when(store.accept(event)).thenReturn(TaskEventStore.Acceptance.ACCEPTED,TaskEventStore.Acceptance.DUPLICATE);
        doThrow(new IOException("channel lost after commit")).doNothing().when(channel).basicAck(7,false);
        assertThrows(IOException.class,()->consumer.onMessage(valid(),channel)); consumer.onMessage(valid(),channel);
        verify(dispatcher,times(1)).signal(); verify(store,times(2)).accept(event);
    }
    @Test void transientDatabaseFailureRetriesBeforeAnyAck() throws Exception {
        when(store.accept(event)).thenThrow(new DataAccessResourceFailureException("injected")).thenReturn(TaskEventStore.Acceptance.ACCEPTED);
        consumer.onMessage(valid(),channel);
        var order=inOrder(store,channel); order.verify(store,times(2)).accept(event); order.verify(channel).basicAck(7,false);
        verify(channel,never()).basicReject(anyLong(),anyBoolean());
    }
    @Test void databaseRetryExhaustionGoesToDlqWithoutHotRequeueOrAcknowledgement() throws Exception {
        when(store.accept(event)).thenThrow(new DataAccessResourceFailureException("injected"));
        consumer.onMessage(valid(),channel);
        verify(store,times(3)).accept(event); verify(channel).basicReject(7,false);
        verify(channel,never()).basicAck(anyLong(),anyBoolean()); verifyNoInteractions(dispatcher);
    }
    @Test void forgedIdentityIsRejectedImmediatelyWithoutRetry() throws Exception {
        when(store.accept(event)).thenThrow(new IllegalArgumentException("snapshot mismatch"));
        consumer.onMessage(valid(),channel);
        verify(store).accept(event); verify(channel).basicReject(7,false); verifyNoInteractions(dispatcher);
    }
    @Test void malformedAndOversizedDocumentsNeverReachDatabase() throws Exception {
        String json=mapper.writeValueAsString(event);
        List<String> invalid=List.of("", " ", "null", "[]", "{}", json+" {}", json.replace("\"schemaVersion\":1","\"schemaVersion\":\"1\""),
            json.replace("\"schemaVersion\":1","\"schemaVersion\":1.0"), json.replace("\"schemaVersion\":1","\"schemaVersion\":2"),
            json.replace("\"eventId\":\"event\"","\"eventId\":\"event\",\"eventId\":\"event\""),
            json.replace("\"eventId\":\"event\"","\"eventId\":\"other\""), json.replace("{","{\"payload\":\"execute something\","), "x".repeat(4097));
        for (String body:invalid) consumer.onMessage(message(body.getBytes(StandardCharsets.UTF_8)),channel);
        verify(channel,times(invalid.size())).basicReject(7,false); verifyNoInteractions(store,dispatcher);
    }
    @Test void missingEnvelopeIdentityIsNotInferredFromBody() throws Exception {
        Message message=valid(); message.getMessageProperties().setMessageId(null);
        consumer.onMessage(message,channel); verify(channel).basicReject(7,false); verifyNoInteractions(store,dispatcher);
    }
}
