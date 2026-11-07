package io.lablens.workspace;

import java.util.LinkedHashMap;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/workspaces/{workspace}/imports")
public class BulkImportController {
    private final BulkImportStore store;
    public BulkImportController(BulkImportStore store) { this.store=store; }
    @PostMapping public ResponseEntity<?> submit(@PathVariable String workspace,@RequestHeader("Idempotency-Key") String key,@RequestBody BulkImportRequests.Batch input) {
        var submitted=store.submit(workspace,key,input);
        var result=new LinkedHashMap<>(submitted.batch()); result.put("reused",submitted.reused());
        return ResponseEntity.status(submitted.reused()?200:202).body(result);
    }
    @GetMapping("/{id}") public Object get(@PathVariable String workspace,@PathVariable String id) { return store.get(workspace,id); }
    @PostMapping("/{id}/cancel") public Object cancel(@PathVariable String workspace,@PathVariable String id) { return store.cancel(workspace,id); }
}
