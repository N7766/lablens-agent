package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import com.rabbitmq.client.Connection;
import com.rabbitmq.client.GetResponse;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.env.Environment;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/** Real MySQL/Redis/RabbitMQ; no recovery poll can make a broken message path pass. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.mq.enabled=true",
    "lablens.mq.relay-enabled=false","lablens.mq.consumer-auto-startup=false",
    "lablens.max-pending-tasks=2000","lablens.max-pending-global=2000","lablens.rate-limit=10000","lablens.cache-namespace=round11-mq-tests"})
@EnabledIfEnvironmentVariable(named="LABLENS_MQ_INTEGRATION_TESTS",matches="true")
class TaskMqIntegrationTest {
    private static final String CONTEXT_NAMESPACE="round11.context."+UUID.randomUUID();
    @DynamicPropertySource static void isolateSpringTopology(DynamicPropertyRegistry properties) {
        // Spring's own RabbitAdmin declares a fresh topology too; manual per-test names alone
        // cannot isolate it from an older queue with different immutable arguments.
        properties.add("lablens.mq.exchange",()->CONTEXT_NAMESPACE);
        properties.add("lablens.mq.queue",()->CONTEXT_NAMESPACE);
        properties.add("lablens.mq.dead-letter-exchange",()->CONTEXT_NAMESPACE+".dead");
        properties.add("lablens.mq.dead-letter-queue",()->CONTEXT_NAMESPACE+".dead");
    }
    @Autowired WorkspaceStore store;
    @Autowired TaskEventStore events;
    @Autowired JdbcTemplate db;
    @Autowired RedisSupport redis;
    @Autowired RabbitTemplate rabbit;
    @Autowired ObjectMapper mapper;
    @Autowired Environment env;
    private Connection connection;
    private Channel channel;
    private String exchange,queue,deadExchange,deadQueue;
    private final List<String> workspaces=new ArrayList<>();
    private static final String ROUTING=TaskEventStore.EVENT_TYPE;
    private static final String RESTART_QUEUE="lablens.round11.restart.proof";
    @BeforeEach void setup() throws Exception {
        var factory=new com.rabbitmq.client.ConnectionFactory();
        factory.setHost(env.getRequiredProperty("spring.rabbitmq.host"));
        factory.setPort(env.getRequiredProperty("spring.rabbitmq.port",Integer.class));
        factory.setUsername(env.getRequiredProperty("spring.rabbitmq.username"));
        factory.setPassword(env.getRequiredProperty("spring.rabbitmq.password"));
        factory.setVirtualHost(env.getRequiredProperty("spring.rabbitmq.virtual-host"));
        factory.setAutomaticRecoveryEnabled(false);
        factory.setConnectionTimeout(3000);
        connection=factory.newConnection("round11-manual-acceptance"); channel=connection.createChannel();
        String suffix=UUID.randomUUID().toString();
        exchange="round11.test."+suffix; queue=exchange; deadExchange=exchange+".dead"; deadQueue=deadExchange;
        // Declare the production topology under unique names, including quorum and bounded DLX semantics.
        var topology=new MqConfiguration().taskEventTopology(exchange,queue,ROUTING,deadExchange,deadQueue,10000);
        for (Declarable declaration:topology.getDeclarables()) {
            if (declaration instanceof Exchange x) channel.exchangeDeclare(x.getName(),x.getType(),x.isDurable(),x.isAutoDelete(),x.getArguments());
            else if (declaration instanceof org.springframework.amqp.core.Queue q) channel.queueDeclare(q.getName(),q.isDurable(),q.isExclusive(),q.isAutoDelete(),q.getArguments());
            else if (declaration instanceof Binding b) channel.queueBind(b.getDestination(),b.getExchange(),b.getRoutingKey(),b.getArguments());
        }
    }
    @AfterEach void cleanup() throws Exception {
        if (connection!=null && connection.isOpen()) {
            if (channel==null || !channel.isOpen()) channel=connection.createChannel();
            for (String q:List.of(queue,deadQueue)) channel.queueDelete(q);
            for (String x:List.of(exchange,deadExchange)) channel.exchangeDelete(x);
            connection.close();
        }
        for (String id:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",id);
            db.update("DELETE FROM documents WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }
    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("round11-mq-synthetic-"+UUID.randomUUID())).get("id");
        workspaces.add(id);
        store.putDocument(id,"evidence",new Requests.Document("Synthetic evidence","A controlled synthetic document; no provider is involved.","test://round11"));
        return id;
    }
    private String task(String workspace,String key) {
        return (String)store.submit(workspace,key,new Requests.Task("Synthetic question "+key,"bm25","extractive",1)).task().get("id");
    }
    private TaskEventStore.Event event(String task) {
        return db.queryForObject("SELECT * FROM task_event_outbox WHERE task_id=?",(rs,n)->new TaskEventStore.Event(
            rs.getString("event_id"),rs.getString("task_id"),rs.getString("workspace_id"),rs.getString("snapshot_ref"),rs.getInt("schema_version"),rs.getString("event_type")),task);
    }
    private TaskEventRelay relay(String routing,int batch,int maxAttempts) {
        return new TaskEventRelay(events,rabbit,mapper,new SimpleMeterRegistry(),exchange,routing,3000,15,maxAttempts,batch);
    }
    private Message message(TaskEventStore.Event event) throws Exception {
        var props=new MessageProperties(); props.setContentType(MessageProperties.CONTENT_TYPE_JSON);
        props.setMessageId(event.eventId()); props.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
        return new Message(mapper.writeValueAsBytes(event),props);
    }
    private void publish(TaskEventStore.Event event) throws Exception { publish(message(event),exchange,ROUTING); }
    private void publish(Message message,String target,String routing) throws Exception {
        var correlation=new CorrelationData(UUID.randomUUID().toString());
        rabbit.send(target,routing,message,correlation);
        assertTrue(correlation.getFuture().get(5,TimeUnit.SECONDS).isAck(),"Real broker must confirm the persistent message");
        assertNull(correlation.getReturned(),"Valid route must not be returned");
    }
    private GetResponse get(Channel source,String q) throws Exception {
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);
        do { GetResponse found=source.basicGet(q,false); if (found!=null) return found; Thread.sleep(10); }
        while(System.nanoTime()<deadline);
        fail("No message received from isolated queue before deadline"); return null;
    }
    private Message delivered(GetResponse response) {
        var p=new MessageProperties(); p.setMessageId(response.getProps().getMessageId());
        p.setContentType(response.getProps().getContentType()); p.setDeliveryTag(response.getEnvelope().getDeliveryTag());
        p.setRedelivered(response.getEnvelope().isRedeliver()); p.setReceivedRoutingKey(response.getEnvelope().getRoutingKey());
        p.setReceivedExchange(response.getEnvelope().getExchange());
        return new Message(response.getBody(),p);
    }
    private TaskEventConsumer consumer(TaskDispatcher dispatcher) {
        return new TaskEventConsumer(events,dispatcher,mapper,new SimpleMeterRegistry(),3,1);
    }
    private int count(String table,String workspace) {
        String field=table.equals("task_event_inbox")?"task_id IN (SELECT id FROM tasks WHERE workspace_id=?)":"workspace_id=?";
        return db.queryForObject("SELECT COUNT(*) FROM "+table+" WHERE "+field,Integer.class,workspace);
    }
    private void evidence(String name,Map<String,Object> data) throws Exception {
        var record=new LinkedHashMap<String,Object>(); record.put("scenario",name); record.put("external_model_calls",0);
        record.put("synthetic",true); record.put("broker_topology","single-node production quorum queues"); record.put("spring_context_topology_namespace",CONTEXT_NAMESPACE); record.putAll(data);
        String directory=System.getenv().getOrDefault("LABLENS_MQ_EVIDENCE_DIR","target/round11-evidence");
        Path path=Path.of(directory); Files.createDirectories(path);
        mapper.writerWithDefaultPrettyPrinter().writeValue(path.resolve(name+".json").toFile(),record);
        // The per-event ledger is saved in full, while console output stays bounded.
        System.out.println("MQ_EVIDENCE "+mapper.writeValueAsString(data.containsKey("rows")
            ?Map.of("scenario",name,"ledger_rows",((List<?>)data.get("rows")).size(),"external_model_calls",0):record));
    }
    private Map<String,Object> latency(String field,String join,String workspace) {
        List<Long> values=db.queryForList("SELECT TIMESTAMPDIFF(MICROSECOND,o.created_at,"+field+") FROM task_event_outbox o "+join+" WHERE o.workspace_id=? ORDER BY 1",Long.class,workspace);
        return Map.of("unit","microseconds","start","outbox.created_at","end",field,"count",values.size(),
            "p50",values.get((int)Math.ceil(values.size()*.50)-1),"p95",values.get((int)Math.ceil(values.size()*.95)-1),"p99",values.get((int)Math.ceil(values.size()*.99)-1));
    }

    @Test void thousandRealBrokerEventsReconcileAndConcurrentBusinessClaimsRemainUnique() throws Exception {
        String workspace=workspace(); long started=System.nanoTime();
        var ids=new HashSet<String>(); for(int i=0;i<1000;i++) ids.add(task(workspace,"batch-"+i));
        assertEquals(1000,ids.size()); assertEquals(1000,count("task_event_outbox",workspace));
        var relay=relay(ROUTING,100,5);
        try { for(int i=0;i<10;i++) relay.relayBatch(); } finally { relay.shutdown(); }
        assertEquals(1000,db.queryForObject("SELECT COUNT(*) FROM task_event_outbox WHERE workspace_id=? AND state='PUBLISHED' AND attempts=1",Integer.class,workspace));
        var dispatcher=mock(TaskDispatcher.class); var consumer=consumer(dispatcher);
        for(int i=0;i<1000;i++) consumer.onMessage(delivered(get(channel,queue)),channel);
        assertEquals(1000,count("task_event_inbox",workspace)); verify(dispatcher,times(1000)).signal();
        assertEquals(0,channel.queueDeclarePassive(queue).getMessageCount());
        Set<String> claimed=ConcurrentHashMap.newKeySet(); AtomicInteger completed=new AtomicInteger();
        try(var pool=Executors.newFixedThreadPool(8)) {
            var futures=new ArrayList<Future<?>>();
            for(int worker=0;worker<8;worker++) { final String owner="round11-synthetic-"+worker; futures.add(pool.submit(()->{
                while(true) { var task=store.claim(owner,60); if(task==null) return;
                    assertEquals(workspace,task.workspace()); assertTrue(claimed.add(task.id()),"No duplicate business claim");
                    assertTrue(store.finish(task,owner,"{\"controlled_executor\":true}",store.encode(WorkerResults.zeroUsage(false)),false,0)); completed.incrementAndGet();
                }
            })); }
            for(var future:futures) future.get(60,TimeUnit.SECONDS);
        }
        assertEquals(ids,claimed); assertEquals(1000,completed.get());
        assertEquals(1000,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=? AND status='SUCCEEDED' AND usage_unknown=0",Integer.class,workspace));
        List<Map<String,Object>> ledger=db.queryForList("SELECT o.event_id,o.task_id,o.workspace_id,o.state AS outbox_state,o.attempts,o.created_at,o.published_at,i.accepted_at,i.disposition AS inbox_disposition,t.status AS task_status,t.owner,t.usage_unknown FROM task_event_outbox o JOIN task_event_inbox i ON i.event_id=o.event_id JOIN tasks t ON t.id=o.task_id WHERE o.workspace_id=? ORDER BY o.event_id",workspace);
        assertEquals(1000,ledger.size());
        evidence("thousand-events-ledger",Map.of("rows",ledger,"unique_claimed_task_ids",claimed.stream().sorted().toList(),
            "measurement_layout","All 1000 tasks submitted first, then sequential confirmed publication, then manual receipt, then 8 concurrent synthetic claim/finish workers. Latency includes these phase waits."));
        evidence("thousand-events",Map.of("submitted_events",1000,"published_events",1000,"inbox_rows",1000,"unique_business_claims",claimed.size(),
            "synthetic_business_completions",completed.get(),"queued_messages_after",0,"elapsed_ms",TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-started),
            "publication_latency",latency("o.published_at","",workspace),"receipt_latency",latency("i.accepted_at","JOIN task_event_inbox i ON i.event_id=o.event_id",workspace)));
    }

    @Test void mandatoryReturnCannotBeMarkedPublishedEvenWhenBrokerConfirms() throws Exception {
        String workspace=workspace(); var event=event(task(workspace,"unroutable"));
        var relay=relay("unbound.route",1,2);
        try { relay.relayBatch(); } finally { relay.shutdown(); }
        Map<String,Object> row=db.queryForMap("SELECT state,attempts,last_error FROM task_event_outbox WHERE event_id=?",event.eventId());
        assertEquals("RETRY",row.get("state")); assertEquals(1,((Number)row.get("attempts")).intValue()); assertNotNull(row.get("last_error"));
        assertEquals(0,channel.queueDeclarePassive(queue).getMessageCount());
        // Advance only the retry eligibility clock; no real delay or hidden transport retry.
        db.update("UPDATE task_event_outbox SET next_attempt_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE event_id=?",event.eventId());
        relay=relay("unbound.route",1,2); try { relay.relayBatch(); } finally { relay.shutdown(); }
        assertEquals("DEAD",db.queryForObject("SELECT state FROM task_event_outbox WHERE event_id=?",String.class,event.eventId()));
        assertEquals(2,db.queryForObject("SELECT attempts FROM task_event_outbox WHERE event_id=?",Integer.class,event.eventId()));
        evidence("mandatory-return",Map.of("attempts",2,"final_state","DEAD","inbox_rows",count("task_event_inbox",workspace),"first_failure",row));
    }

    @Test void confirmedSendBeforeDatabaseMarkIsRedeliveredWithOneInboxWrite() throws Exception {
        String workspace=workspace(); var event=event(task(workspace,"confirm-gap"));
        var claimed=events.claim("publisher-before-gap",1,5).orElseThrow(); assertEquals(event,claimed.event());
        publish(event); // Real confirmation observed, deliberately skip events.published().
        db.update("UPDATE task_event_outbox SET lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)),next_attempt_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE event_id=?",event.eventId());
        var relay=relay(ROUTING,1,5); try { relay.relayBatch(); } finally { relay.shutdown(); }
        var dispatcher=mock(TaskDispatcher.class); var consumer=consumer(dispatcher);
        consumer.onMessage(delivered(get(channel,queue)),channel); consumer.onMessage(delivered(get(channel,queue)),channel);
        assertEquals(1,count("task_event_inbox",workspace)); verify(dispatcher,times(1)).signal();
        assertEquals(2,db.queryForObject("SELECT attempts FROM task_event_outbox WHERE event_id=?",Integer.class,event.eventId()));
        assertFalse(events.published(claimed,"publisher-before-gap"),"Old publisher cannot commit after the lease was reclaimed");
        evidence("confirm-before-db-mark-gap",Map.of("injection","component step: skip database mark, advance expired lease","real_deliveries",2,"inbox_rows",1,"dispatcher_signals",1,"attempts",2));
    }

    @Test void inboxCommitThenChannelLossRedeliversWithoutRepeatedWake() throws Exception {
        String workspace=workspace(); var event=event(task(workspace,"ack-gap")); publish(event);
        GetResponse first=get(channel,queue);
        assertEquals(TaskEventStore.Acceptance.ACCEPTED,events.accept(mapper.readValue(first.getBody(),TaskEventStore.Event.class)));
        assertEquals(1,count("task_event_inbox",workspace));
        channel.close(); // Actual broker channel loss with outstanding manual ACK.
        channel=connection.createChannel(); var replay=get(channel,queue); assertTrue(replay.getEnvelope().isRedeliver());
        var dispatcher=mock(TaskDispatcher.class); consumer(dispatcher).onMessage(delivered(replay),channel);
        assertEquals(1,count("task_event_inbox",workspace)); verifyNoInteractions(dispatcher);
        assertEquals(0,channel.queueDeclarePassive(queue).getMessageCount());
        assertEquals("QUEUED",store.task(workspace,event.taskId()).get("status"));
        evidence("inbox-before-ack-gap",Map.of("injection","component commit then real channel close without ACK","redelivered",true,"inbox_rows",1,"task_state","QUEUED","duplicate_dispatcher_signals",0,"limitation","Process failure after inbox commit but before signal relies on the durable task recovery poll; this scenario does not prove a JVM process restart."));
    }

    @Test void reverseOrderDuplicateAndLateUnknownResultNotificationsCannotResurrectTasks() throws Exception {
        String workspace=workspace(); var a=event(task(workspace,"first")); var b=event(task(workspace,"second")); var c=event(task(workspace,"unknown"));
        db.update("UPDATE tasks SET status='FAILED',usage_unknown=1,execution_usage_json=?,error_code='worker_interrupted' WHERE id=?",store.encode(WorkerResults.uncertainUsage(true)),c.taskId());
        publish(c); publish(b); publish(a); publish(b); publish(c);
        var dispatcher=mock(TaskDispatcher.class); var consumer=consumer(dispatcher);
        for(int i=0;i<5;i++) consumer.onMessage(delivered(get(channel,queue)),channel);
        assertEquals(3,count("task_event_inbox",workspace)); verify(dispatcher,times(2)).signal();
        assertEquals("FAILED",store.task(workspace,c.taskId()).get("status")); assertEquals(true,store.task(workspace,c.taskId()).get("usage_unknown"));
        assertEquals("NOT_QUEUED",db.queryForObject("SELECT disposition FROM task_event_inbox WHERE event_id=?",String.class,c.eventId()));
        evidence("duplicate-reverse-terminal",Map.of("deliveries",5,"inbox_rows",3,"queued_task_signals",2,"terminal_task_status","FAILED","terminal_usage_unknown",true));
    }

    @Test void productionAdminRejectsExistingQueueWithIncompatibleType() throws Exception {
        String legacy=exchange+".legacy-classic";
        channel.queueDeclare(legacy,true,false,false,Map.of("x-queue-type","classic"));
        try {
            var configuration=new MqConfiguration();
            var topology=configuration.taskEventTopology(exchange,legacy,ROUTING,deadExchange,deadQueue,10000);
            org.springframework.amqp.core.Queue required=topology.getDeclarables().stream()
                .filter(item->item instanceof org.springframework.amqp.core.Queue q && q.getName().equals(legacy))
                .map(item->(org.springframework.amqp.core.Queue)item).findFirst().orElseThrow();
            // Use the actual production admin policy; swallowing declaration errors makes this assertion fail.
            var admin=configuration.taskEventAdmin(rabbit.getConnectionFactory());
            var failure=assertThrows(org.springframework.amqp.AmqpException.class,()->admin.declareQueue(required));
            boolean precondition=false;
            for(Throwable cause=failure;cause!=null;cause=cause.getCause()) {
                if(cause instanceof com.rabbitmq.client.ShutdownSignalException signal
                    && signal.getReason() instanceof com.rabbitmq.client.AMQP.Channel.Close close
                    && close.getReplyCode()==406) precondition=true;
            }
            assertTrue(precondition,"Must observe the real broker's PRECONDITION_FAILED response");
            assertEquals(0,channel.queueDeclarePassive(legacy).getMessageCount(),"The mismatched existing queue is preserved, not silently recreated");
            evidence("topology-mismatch",Map.of("existing_queue_type","classic","required_queue_type","quorum","broker_reply_code",406,"declaration_error_propagated",true));
        } finally { channel.queueDelete(legacy); }
    }

    @Test void malformedPayloadAndBoundedDatabaseFailuresReachRealDeadLetterQueue() throws Exception {
        var props=new MessageProperties(); props.setMessageId(UUID.randomUUID().toString()); props.setContentType("application/json"); props.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
        publish(new Message("{bad-json".getBytes(StandardCharsets.UTF_8),props),exchange,ROUTING);
        var dispatcher=mock(TaskDispatcher.class); consumer(dispatcher).onMessage(delivered(get(channel,queue)),channel);
        var malformed=get(channel,deadQueue); assertArrayEquals("{bad-json".getBytes(StandardCharsets.UTF_8),malformed.getBody()); channel.basicAck(malformed.getEnvelope().getDeliveryTag(),false);
        String workspace=workspace(); var event=event(task(workspace,"db-unavailable")); publish(event);
        var unavailable=spy(events); doThrow(new CannotAcquireLockException("Controlled database outage")).when(unavailable).accept(any());
        var consumer=new TaskEventConsumer(unavailable,dispatcher,mapper,new SimpleMeterRegistry(),3,1);
        consumer.onMessage(delivered(get(channel,queue)),channel);
        var rejected=get(channel,deadQueue); assertEquals(event.eventId(),rejected.getProps().getMessageId()); channel.basicAck(rejected.getEnvelope().getDeliveryTag(),false);
        verify(unavailable,times(3)).accept(event); verifyNoInteractions(dispatcher); assertEquals(0,count("task_event_inbox",workspace));
        evidence("bounded-dead-letter",Map.of("malformed_dead_letters",1,"database_failure_dead_letters",1,"database_attempts",3,"inbox_rows",0,"database_failure_injection","controlled exception at database component boundary; transport and dead-letter queue are real"));
    }

    @Test void onlyActualMqReceiptWakesRealDispatcherWithNoPeriodicScheduler() throws Exception {
        String workspace=workspace(); String task=task(workspace,"mq-only-wake"); var event=event(task);
        AtomicInteger requests=new AtomicInteger(); CountDownLatch received=new CountDownLatch(1);
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/answer",exchange->{
            requests.incrementAndGet(); exchange.getRequestBody().readAllBytes();
            byte[] response=mapper.writeValueAsBytes(Map.of("status","excerpts","mode","extractive","retrieval",Map.of("snapshot_id",workspace+":1","strategy","bm25"),"usage",WorkerResults.zeroUsage(false)));
            exchange.getResponseHeaders().add("Content-Type","application/json"); exchange.sendResponseHeaders(200,response.length);
            exchange.getResponseBody().write(response); exchange.close(); received.countDown();
        });
        server.start(); var metrics=new SimpleMeterRegistry();
        var dispatcher=new TaskDispatcher(store,redis,metrics,"http://127.0.0.1:"+server.getAddress().getPort(),2,20,5,true);
        try {
            // Directly constructed object has no @Scheduled registration and receives no submission signal.
            Thread.sleep(300); assertEquals(0,requests.get()); assertEquals("QUEUED",store.task(workspace,task).get("status"));
            var relay=relay(ROUTING,1,5); try { relay.relayBatch(); } finally { relay.shutdown(); }
            consumer(dispatcher).onMessage(delivered(get(channel,queue)),channel);
            assertTrue(received.await(5,TimeUnit.SECONDS));
            long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);
            while(!"SUCCEEDED".equals(store.task(workspace,task).get("status")) && System.nanoTime()<deadline) Thread.sleep(10);
            assertEquals("SUCCEEDED",store.task(workspace,task).get("status"));
            publish(event); consumer(dispatcher).onMessage(delivered(get(channel,queue)),channel); Thread.sleep(100);
            assertEquals(1,requests.get()); assertEquals(1,count("task_event_inbox",workspace)); assertNull(store.claim("later-notification",20));
            evidence("mq-only-dispatcher",Map.of("scheduled_recovery_poll",false,"direct_submission_signal",false,"actual_inbox_rows",1,"local_fixed_worker_requests",1,"task_status","SUCCEEDED","delivery_count",2));
        } finally { dispatcher.shutdown(); server.stop(0); }
    }

    @Test @EnabledIfEnvironmentVariable(named="LABLENS_MQ_RESTART_PHASE",matches="prepare")
    void brokerRestartPrepare() throws Exception {
        Path marker=Path.of(Objects.requireNonNull(System.getenv("LABLENS_MQ_RESTART_MARKER"),"Set an isolated marker file for real broker restart"));
        assertFalse(Files.exists(marker),"Restart marker cannot be overwritten");
        channel.queueDeclare(RESTART_QUEUE,true,false,false,Map.of("x-queue-type","quorum","x-max-length",10000,"x-overflow","reject-publish")); channel.queuePurge(RESTART_QUEUE);
        String id=UUID.randomUUID().toString(); byte[] body=mapper.writeValueAsBytes(Map.of("synthetic",true,"probe_id",id,"external_model_calls",0));
        var properties=new MessageProperties(); properties.setMessageId(id); properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT); properties.setContentType("application/json");
        publish(new Message(body,properties),"",RESTART_QUEUE);
        Files.createDirectories(marker.toAbsolutePath().getParent());
        mapper.writeValue(marker.toFile(),Map.of("message_id",id,"body_base64",Base64.getEncoder().encodeToString(body),"queue",RESTART_QUEUE));
        assertEquals(1,channel.queueDeclarePassive(RESTART_QUEUE).getMessageCount());
        evidence("broker-restart-prepare",Map.of("confirmed_persistent_messages",1,"durable_queue",RESTART_QUEUE,"message_id",id,"restart_performed",false));
    }
    @Test @EnabledIfEnvironmentVariable(named="LABLENS_MQ_RESTART_PHASE",matches="verify")
    void brokerRestartVerify() throws Exception {
        Path marker=Path.of(Objects.requireNonNull(System.getenv("LABLENS_MQ_RESTART_MARKER")));
        Map<?,?> expected=mapper.readValue(marker.toFile(),Map.class);
        // Passive declaration must not recreate the queue and hide persistence loss.
        assertEquals(1,channel.queueDeclarePassive(RESTART_QUEUE).getMessageCount());
        var message=get(channel,RESTART_QUEUE); assertEquals(expected.get("message_id"),message.getProps().getMessageId());
        assertArrayEquals(Base64.getDecoder().decode((String)expected.get("body_base64")),message.getBody());
        assertEquals(2,message.getProps().getDeliveryMode()); channel.basicAck(message.getEnvelope().getDeliveryTag(),false);
        assertEquals(0,channel.queueDeclarePassive(RESTART_QUEUE).getMessageCount()); channel.queueDelete(RESTART_QUEUE);
        evidence("broker-restart-verify",Map.of("verified_persistent_messages",1,"message_id",expected.get("message_id"),"requires_external_evidence","A successful verify must be paired with independently recorded actual broker stop/start evidence."));
    }
}
