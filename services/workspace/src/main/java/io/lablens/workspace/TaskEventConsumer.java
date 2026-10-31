package io.lablens.workspace;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import io.micrometer.core.instrument.MeterRegistry;
import java.io.IOException;
import java.util.HashSet;
import java.util.Set;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.listener.api.ChannelAwareMessageListener;
import org.springframework.dao.DataAccessException;

/** Persist inbox receipt before ACK; the only execution side effect is a bounded dispatcher wakeup. */
public final class TaskEventConsumer implements ChannelAwareMessageListener {
    static final int MAX_BODY_BYTES=4096;
    private static final Set<String> FIELDS=Set.of("eventId","taskId","workspaceId","snapshotRef","schemaVersion","eventType");
    private final TaskEventStore store;
    private final TaskDispatcher dispatcher;
    private final ObjectMapper mapper;
    private final MeterRegistry metrics;
    private final int databaseAttempts,databaseBackoffMs;

    public TaskEventConsumer(TaskEventStore store,TaskDispatcher dispatcher,ObjectMapper mapper,MeterRegistry metrics,
                             int databaseAttempts,int databaseBackoffMs) {
        if (databaseAttempts<1 || databaseAttempts>5 || databaseBackoffMs<1 || databaseBackoffMs>1000)
            throw new IllegalArgumentException("Invalid bounded inbox retry limits");
        this.store=store; this.dispatcher=dispatcher; this.metrics=metrics;
        this.mapper=mapper.copy().enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
        this.mapper.getFactory().enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
        this.databaseAttempts=databaseAttempts; this.databaseBackoffMs=databaseBackoffMs;
    }

    @Override public void onMessage(Message message,Channel channel) throws IOException {
        long tag=message.getMessageProperties().getDeliveryTag();
        TaskEventStore.Event event;
        try { event=parse(message); }
        catch (IOException | IllegalArgumentException e) { reject(channel,tag,"invalid_schema"); return; }
        TaskEventStore.Acceptance accepted=null;
        for (int attempt=1;attempt<=databaseAttempts;attempt++) {
            try { accepted=store.accept(event); break; }
            catch (IllegalArgumentException e) { reject(channel,tag,"invalid_identity"); return; }
            catch (DataAccessException e) {
                metrics.counter("lablens.mq.consume.database_failures").increment();
                if (attempt==databaseAttempts) { reject(channel,tag,"database_retry_exhausted"); return; }
                try { Thread.sleep((long)databaseBackoffMs*attempt); }
                catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt(); reject(channel,tag,"interrupted"); return;
                }
            } catch (RuntimeException e) { reject(channel,tag,"consumer_failed"); return; }
        }
        // TaskEventStore's transaction has committed on return. Redelivery after ACK loss is harmless.
        if (accepted==TaskEventStore.Acceptance.ACCEPTED) dispatcher.signal();
        channel.basicAck(tag,false);
        metrics.counter("lablens.mq.consume.receipts","outcome",accepted.name().toLowerCase(java.util.Locale.ROOT)).increment();
    }

    private TaskEventStore.Event parse(Message message) throws IOException {
        var props=message.getMessageProperties();
        if (message.getBody().length==0 || message.getBody().length>MAX_BODY_BYTES
                || !MessageProperties.CONTENT_TYPE_JSON.equals(props.getContentType()))
            throw new IllegalArgumentException("Invalid event envelope");
        JsonNode tree=mapper.readTree(message.getBody());
        if (tree==null || !tree.isObject()) throw new IllegalArgumentException("Invalid event document");
        Set<String> fields=new HashSet<>(); tree.fieldNames().forEachRemaining(fields::add);
        if (!tree.isObject() || !fields.equals(FIELDS)) throw new IllegalArgumentException("Invalid event fields");
        for (String field:FIELDS) {
            if (field.equals("schemaVersion")) continue;
            if (!tree.get(field).isTextual() || tree.get(field).textValue().isBlank() || tree.get(field).textValue().length()>200)
                throw new IllegalArgumentException("Invalid event reference");
        }
        if (!tree.get("schemaVersion").isIntegralNumber() || !tree.get("schemaVersion").canConvertToInt()
                || tree.get("schemaVersion").intValue()!=1 || !TaskEventStore.EVENT_TYPE.equals(tree.get("eventType").textValue())
                || !tree.get("eventId").textValue().equals(props.getMessageId()))
            throw new IllegalArgumentException("Invalid event identity");
        return mapper.treeToValue(tree,TaskEventStore.Event.class);
    }

    private void reject(Channel channel,long tag,String reason) throws IOException {
        channel.basicReject(tag,false);
        metrics.counter("lablens.mq.consume.rejected","reason",reason).increment();
    }
}
