package io.lablens.workspace;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.Map;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitAdmin;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

@Configuration(proxyBeanMethods=false)
@ConditionalOnProperty(name="lablens.mq.enabled",havingValue="true")
public class MqConfiguration {
    @Bean public Declarables taskEventTopology(
            @Value("${lablens.mq.exchange}") String exchange,@Value("${lablens.mq.queue}") String queue,
            @Value("${lablens.mq.routing-key}") String routingKey,
            @Value("${lablens.mq.dead-letter-exchange}") String deadExchange,
            @Value("${lablens.mq.dead-letter-queue}") String deadQueue,
            @Value("${lablens.mq.max-queue-length}") int maxLength) {
        if (maxLength<1 || maxLength>1000000) throw new IllegalArgumentException("Invalid queue bound");
        var main=new Queue(queue,true,false,false,Map.of("x-queue-type","quorum","x-max-length",maxLength,
            "x-max-length-bytes",(long)maxLength*TaskEventConsumer.MAX_BODY_BYTES,
            "x-overflow","reject-publish","x-dead-letter-exchange",deadExchange,"x-dead-letter-routing-key",routingKey,
            "x-dead-letter-strategy","at-least-once","x-delivery-limit",5));
        var dead=new Queue(deadQueue,true,false,false,Map.of("x-queue-type","quorum","x-max-length",maxLength,
            "x-max-length-bytes",(long)maxLength*TaskEventConsumer.MAX_BODY_BYTES,"x-overflow","reject-publish"));
        return new Declarables(new DirectExchange(exchange,true,false),new DirectExchange(deadExchange,true,false),main,dead,
            new Binding(queue,Binding.DestinationType.QUEUE,exchange,routingKey,null),
            new Binding(deadQueue,Binding.DestinationType.QUEUE,deadExchange,routingKey,null));
    }

    @Bean public RabbitAdmin taskEventAdmin(ConnectionFactory connectionFactory) {
        var admin=new RabbitAdmin(connectionFactory); admin.setIgnoreDeclarationExceptions(false); return admin;
    }

    @Bean public RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory,
            @Qualifier("taskEventClientExecutor") ThreadPoolTaskExecutor clientExecutor) {
        if (!(connectionFactory instanceof CachingConnectionFactory caching))
            throw new IllegalArgumentException("Task transport requires correlated confirms");
        caching.setPublisherConfirmType(CachingConnectionFactory.ConfirmType.CORRELATED);
        caching.setPublisherReturns(true);
        caching.setExecutor(clientExecutor);
        var template=new RabbitTemplate(connectionFactory); template.setMandatory(true);
        template.setUsePublisherConnection(true); return template;
    }

    @Bean public TaskEventRelay taskEventRelay(TaskEventStore store,RabbitTemplate rabbit,ObjectMapper mapper,MeterRegistry metrics,
            @Value("${lablens.mq.exchange}") String exchange,@Value("${lablens.mq.routing-key}") String routingKey,
            @Value("${lablens.mq.confirm-timeout-ms}") int timeout,@Value("${lablens.mq.lease-seconds}") int lease,
            @Value("${lablens.mq.max-attempts}") int attempts,@Value("${lablens.mq.batch-size}") int batch) {
        return new TaskEventRelay(store,rabbit,mapper,metrics,exchange,routingKey,timeout,lease,attempts,batch);
    }

    @Bean public TaskEventConsumer taskEventConsumer(TaskEventStore store,TaskDispatcher dispatcher,ObjectMapper mapper,MeterRegistry metrics,
            @Value("${lablens.mq.database-attempts}") int attempts,@Value("${lablens.mq.database-backoff-ms}") int backoff) {
        return new TaskEventConsumer(store,dispatcher,mapper,metrics,attempts,backoff);
    }

    @Bean public ThreadPoolTaskExecutor taskEventListenerExecutor() {
        var executor=new ThreadPoolTaskExecutor(); executor.setCorePoolSize(1); executor.setMaxPoolSize(1);
        executor.setQueueCapacity(1); executor.setThreadNamePrefix("lablens-inbox-"); return executor;
    }

    @Bean public ThreadPoolTaskExecutor taskEventClientExecutor() {
        var executor=new ThreadPoolTaskExecutor(); executor.setCorePoolSize(4); executor.setMaxPoolSize(4);
        executor.setQueueCapacity(32); executor.setThreadNamePrefix("lablens-amqp-"); return executor;
    }

    @Bean public SimpleMessageListenerContainer taskEventListener(ConnectionFactory connectionFactory,RabbitAdmin admin,RabbitTemplate rabbitTemplate,
            TaskEventConsumer consumer,@Qualifier("taskEventListenerExecutor") ThreadPoolTaskExecutor executor,
            @Value("${lablens.mq.queue}") String queue,@Value("${lablens.mq.prefetch}") int prefetch,
            @Value("${lablens.mq.consumer-auto-startup}") boolean autoStartup) {
        if (prefetch<1 || prefetch>32) throw new IllegalArgumentException("Invalid consumer prefetch");
        var listener=new SimpleMessageListenerContainer(connectionFactory);
        listener.setQueueNames(queue); listener.setAmqpAdmin(admin); listener.setMessageListener(consumer);
        listener.setAcknowledgeMode(AcknowledgeMode.MANUAL); listener.setPrefetchCount(prefetch);
        listener.setConcurrentConsumers(1); listener.setMaxConcurrentConsumers(1); listener.setTaskExecutor(executor);
        listener.setMissingQueuesFatal(false); listener.setPossibleAuthenticationFailureFatal(false);
        listener.setMismatchedQueuesFatal(true);
        listener.setRecoveryInterval(5000); listener.setDeclarationRetries(1); listener.setFailedDeclarationRetryInterval(1000);
        listener.setDefaultRequeueRejected(false); listener.setAutoStartup(autoStartup); return listener;
    }

    @Bean public RelaySchedule taskEventSchedule(TaskEventRelay relay,@Value("${lablens.mq.relay-enabled}") boolean enabled) {
        return new RelaySchedule(relay,enabled);
    }
    static final class RelaySchedule {
        private final TaskEventRelay relay; private final boolean enabled;
        RelaySchedule(TaskEventRelay relay,boolean enabled) { this.relay=relay; this.enabled=enabled; }
        @Scheduled(fixedDelayString="${lablens.mq.relay-poll-ms:500}") public void tick() { if (enabled) relay.signal(); }
    }
}
