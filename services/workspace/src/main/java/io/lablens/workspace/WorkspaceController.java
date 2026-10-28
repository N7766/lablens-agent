package io.lablens.workspace;

import io.micrometer.core.instrument.MeterRegistry;
import jakarta.validation.Valid;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class WorkspaceController {
    private final WorkspaceStore store;
    private final RedisSupport redis;
    private final TaskDispatcher dispatcher;
    private final MeterRegistry metrics;
    public WorkspaceController(WorkspaceStore store,RedisSupport redis,TaskDispatcher dispatcher,MeterRegistry metrics) {
        this.store=store; this.redis=redis; this.dispatcher=dispatcher; this.metrics=metrics;
    }
    @GetMapping("/workspaces") public Object workspaces() { return Map.of("workspaces",store.workspaces()); }
    @PostMapping("/workspaces") public ResponseEntity<?> create(@Valid @RequestBody Requests.Workspace input) { return ResponseEntity.status(201).body(store.createWorkspace(input)); }
    @GetMapping("/workspaces/{id}/documents") public Object documents(@PathVariable String id) { return store.documents(id); }
    @GetMapping("/workspaces/{id}/documents/{documentId}") public Object document(@PathVariable String id,@PathVariable String documentId) { return store.document(id,documentId); }
    @PutMapping("/workspaces/{id}/documents/{documentId}") public Object put(@PathVariable String id,@PathVariable String documentId,@Valid @RequestBody Requests.Document input) { return store.putDocument(id,documentId,input); }
    @DeleteMapping("/workspaces/{id}/documents/{documentId}") public ResponseEntity<?> delete(@PathVariable String id,@PathVariable String documentId) { store.deleteDocument(id,documentId); return ResponseEntity.noContent().build(); }
    @GetMapping("/workspaces/{id}/conversations") public Object conversations(@PathVariable String id) { return Map.of("conversations",store.conversations(id)); }
    @PostMapping("/workspaces/{id}/conversations") public ResponseEntity<?> conversation(@PathVariable String id,@RequestBody Requests.Conversation input) { return ResponseEntity.status(201).body(store.createConversation(id,input)); }
    @GetMapping("/workspaces/{id}/conversations/{conversationId}") public Object conversation(@PathVariable String id,@PathVariable String conversationId) { return store.conversation(id,conversationId); }
    @PostMapping("/workspaces/{id}/conversations/{conversationId}/turns") public ResponseEntity<?> turn(@PathVariable String id,@PathVariable String conversationId,@RequestHeader("Idempotency-Key") String key,@RequestBody Requests.Turn input) {
        WorkspaceStore.Submission result=store.submitTurn(id,conversationId,key,input);
        if (!result.reused()) dispatcher.signal();
        Map<String,Object> body=new LinkedHashMap<>(result.task()); body.put("reused",result.reused());
        return ResponseEntity.status(result.reused()?200:202).body(body);
    }
    @PostMapping("/workspaces/{id}/tasks") public ResponseEntity<?> submit(@PathVariable String id,@RequestHeader("Idempotency-Key") String key,@Valid @RequestBody Requests.Task input) {
        WorkspaceStore.Submission result=store.submit(id,key,input);
        // TransactionTemplate has committed before submit returns. This only schedules a wakeup.
        if (!result.reused()) dispatcher.signal();
        Map<String,Object> body=new LinkedHashMap<>(result.task()); body.put("reused",result.reused());
        return ResponseEntity.status(result.reused()?200:202).body(body);
    }
    @GetMapping("/workspaces/{id}/tasks") public Object tasks(@PathVariable String id) { return Map.of("tasks",store.tasks(id)); }
    @GetMapping("/workspaces/{id}/tasks/{taskId}") public Object task(@PathVariable String id,@PathVariable String taskId) { return store.task(id,taskId); }
    @GetMapping("/system/status") public Object status() {
        Map<String,Object> result=new LinkedHashMap<>(); result.put("version","0.12.0");
        try { result.putAll(store.status()); } catch (DataAccessException e) { result.put("database","unavailable"); }
        result.put("redis",redis.status()); result.put("workers",dispatcher.status());
        result.put("runtime",Map.of("java_version",System.getProperty("java.version"),"vm",System.getProperty("java.vm.name")));
        Map<String,Object> measures=new LinkedHashMap<>();
        metrics.getMeters().stream().filter(meter->meter.getId().getName().startsWith("lablens.")).forEach(meter->{
            Map<String,Double> values=new LinkedHashMap<>(); meter.measure().forEach(m->values.put(m.getStatistic().getTagValueRepresentation(),m.getValue()));
            measures.put(meter.getId().getName()+(meter.getId().getTags().isEmpty()?"":meter.getId().getTags().toString()),values);
        });
        result.put("process_metrics",measures); result.put("metrics_scope","Counters and timers reset on JVM restart; persisted task counts come from MySQL");
        return result;
    }
}
