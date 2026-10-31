package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.test.util.ReflectionTestUtils;

class MqConfigurationTest {
    @Test void disabledTransportRequiresNeitherDatabaseNorBrokerBeans() {
        new ApplicationContextRunner().withUserConfiguration(MqConfiguration.class).withPropertyValues("lablens.mq.enabled=false")
            .run(context->{ assertNull(context.getStartupFailure()); assertEquals(0,context.getBeansOfType(TaskEventRelay.class).size()); });
    }
    @Test void topologyPersistsDeadLettersAndRejectsOverflowInsteadOfDroppingOldTasks() {
        var topology=new MqConfiguration().taskEventTopology("events","tasks","ready","dead.events","dead.tasks",10000);
        var queues=topology.getDeclarablesByType(Queue.class);
        assertEquals(2,queues.size());
        for (var queue:queues) {
            assertTrue(queue.isDurable()); assertFalse(queue.isAutoDelete()); assertFalse(queue.isExclusive());
            assertEquals("reject-publish",queue.getArguments().get("x-overflow"));
        }
        var main=queues.stream().filter(q->q.getName().equals("tasks")).findFirst().orElseThrow();
        assertEquals("quorum",main.getArguments().get("x-queue-type"));
        assertEquals("at-least-once",main.getArguments().get("x-dead-letter-strategy"));
        assertEquals("dead.events",main.getArguments().get("x-dead-letter-exchange"));
    }
    @Test void publisherRequiresReturnsAndCorrelatedConfirmsEvenIfBootPropertiesChange() {
        var factory=mock(CachingConnectionFactory.class);
        var executor=mock(ThreadPoolTaskExecutor.class);
        var template=new MqConfiguration().rabbitTemplate(factory,executor);
        verify(factory).setPublisherConfirmType(CachingConnectionFactory.ConfirmType.CORRELATED);
        verify(factory).setPublisherReturns(true);
        verify(factory).setExecutor(executor);
        assertTrue(template.isUsePublisherConnection());
        assertTrue(template.isMandatoryFor(new org.springframework.amqp.core.Message(new byte[0])));
    }
    @Test void incompatibleTopologyIsFatalButDisconnectedBrokerKeepsRecoveryEnabled() {
        var factory=mock(CachingConnectionFactory.class);
        var config=new MqConfiguration();
        var admin=config.taskEventAdmin(factory);
        var client=mock(ThreadPoolTaskExecutor.class);
        var template=config.rabbitTemplate(factory,client);
        var listener=config.taskEventListener(factory,admin,template,mock(TaskEventConsumer.class),client,"tasks",8,false);
        assertEquals(false,ReflectionTestUtils.getField(admin,"ignoreDeclarationExceptions"));
        assertEquals(true,ReflectionTestUtils.getField(listener,"mismatchedQueuesFatal"));
        assertEquals(false,ReflectionTestUtils.getField(listener,"missingQueuesFatal"));
        assertEquals(false,ReflectionTestUtils.getField(listener,"possibleAuthenticationFailureFatal"));
        assertFalse(listener.isAutoStartup());
    }
    @Test void declarationCheckDistinguishesUnavailableBrokerFromKnownArgumentMismatch() {
        var factory=mock(CachingConnectionFactory.class);
        var config=new MqConfiguration(); var admin=mock(org.springframework.amqp.rabbit.core.RabbitAdmin.class);
        var executor=mock(ThreadPoolTaskExecutor.class);
        var listener=config.taskEventListener(factory,admin,config.rabbitTemplate(factory,executor),
            mock(TaskEventConsumer.class),executor,"tasks",8,false);
        doThrow(new org.springframework.amqp.AmqpConnectException(new java.net.ConnectException("offline"))).when(admin).initialize();
        assertDoesNotThrow(()->ReflectionTestUtils.invokeMethod(listener,"checkMismatchedQueues"));
        var close=new com.rabbitmq.client.AMQP.Channel.Close.Builder().replyCode(406).replyText("inequivalent arg")
            .classId(50).methodId(10).build();
        var shutdown=new com.rabbitmq.client.ShutdownSignalException(false,false,close,null);
        doThrow(new org.springframework.amqp.AmqpIOException(new java.io.IOException(shutdown))).when(admin).initialize();
        assertThrows(org.springframework.amqp.rabbit.listener.exception.FatalListenerStartupException.class,
            ()->ReflectionTestUtils.invokeMethod(listener,"checkMismatchedQueues"));
    }
}
