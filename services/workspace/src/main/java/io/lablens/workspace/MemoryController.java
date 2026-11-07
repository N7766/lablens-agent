package io.lablens.workspace;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/workspaces/{workspace}/memories")
public class MemoryController {
    private final MemoryStore memories;
    private final AuthStore auth;
    public MemoryController(MemoryStore memories,AuthStore auth) { this.memories=memories;this.auth=auth; }
    @GetMapping public Object list(@PathVariable String workspace) { return memories.list(workspace); }
    @PostMapping public ResponseEntity<?> create(@PathVariable String workspace,@RequestBody MemoryRequests.Mutation input) {
        return ResponseEntity.status(201).body(memories.write(workspace,null,input,auth.current()));
    }
    @PutMapping("/{id}") public Object update(@PathVariable String workspace,@PathVariable String id,@RequestBody MemoryRequests.Mutation input) {
        return memories.write(workspace,id,input,auth.current());
    }
    @DeleteMapping("/{id}") public Object delete(@PathVariable String workspace,@PathVariable String id,@RequestBody MemoryRequests.Delete input) {
        return memories.delete(workspace,id,input,auth.current());
    }
}
