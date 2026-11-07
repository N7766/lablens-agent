package io.lablens.workspace;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class RecoveryController {
    private final RecoveryStore recovery;
    private final TaskDispatcher dispatcher;
    private final AuthStore auth;
    public RecoveryController(RecoveryStore recovery,TaskDispatcher dispatcher,AuthStore auth) {
        this.recovery=recovery;this.dispatcher=dispatcher;this.auth=auth;
    }
    @PostMapping("/internal/tasks/{task}/checkpoint")
    public Object checkpoint(@PathVariable String task,@RequestHeader("X-LabLens-Execution-Owner") String owner,
                             @RequestBody RecoveryStore.Checkpoint input) {
        return recovery.checkpoint(task,owner,input);
    }
    @GetMapping("/workspaces/{workspace}/tasks/{task}/recovery")
    public Object recovery(@PathVariable String workspace,@PathVariable String task) { return recovery.status(workspace,task); }
    @PostMapping("/workspaces/{workspace}/tasks/{task}/resume")
    public ResponseEntity<?> resume(@PathVariable String workspace,@PathVariable String task,
                                    @RequestHeader("Idempotency-Key") String key,@RequestBody RecoveryStore.Resume input) {
        var submitted=recovery.resume(workspace,task,key,input,auth.current());
        if (!submitted.reused()) dispatcher.signal();
        Map<String,Object> body=new LinkedHashMap<>(submitted.task());body.put("reused",submitted.reused());
        return ResponseEntity.status(submitted.reused()?200:202).body(body);
    }
}
