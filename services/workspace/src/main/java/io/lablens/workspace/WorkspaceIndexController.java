package io.lablens.workspace;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.Semaphore;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

/** Snapshot-scoped derived indexes. No provider call holds a database transaction. */
@RestController
@RequestMapping("/api/workspaces/{workspace}/index")
public class WorkspaceIndexController {
    private final WorkspaceStore store;
    private final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final String worker;
    @Value("${lablens.service-key:}")
    private String serviceKey="";
    private final Semaphore slots=new Semaphore(2);
    public record Input(String strategy,@JsonProperty("expected_revision") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long expectedRevision,
                        @JsonProperty("approved_revision") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long approvedRevision) {}
    public WorkspaceIndexController(WorkspaceStore store,@Value("${lablens.worker-base-url}") String worker) {
        this.store=store; this.worker=worker.replaceAll("/$","");
    }
    @GetMapping public Object status(@PathVariable String workspace,@RequestParam(defaultValue="bm25") String strategy) {
        return call(workspace,new Input(strategy,null,null),false);
    }
    @PostMapping public Object build(@PathVariable String workspace,@RequestBody Input input) {
        return call(workspace,input,true);
    }
    @SuppressWarnings("unchecked")
    private Object call(String workspace,Input input,boolean build) {
        String strategy=input.strategy()==null?"bm25":input.strategy();
        if (!Set.of("bm25","dense","hybrid").contains(strategy)) throw ApiError.bad("Unknown index strategy");
        Map<String,Object> snapshot=store.documents(workspace);
        long revision=((Number)snapshot.get("revision")).longValue();
        if (build && (input.expectedRevision()==null || input.expectedRevision()!=revision))
            throw new ApiError(HttpStatus.CONFLICT,"revision_conflict","Read the current document revision before building an index.");
        if (build && !"bm25".equals(strategy) && (input.approvedRevision()==null || input.approvedRevision()!=revision))
            throw new ApiError(HttpStatus.CONFLICT,"provider_approval_required","Embedding requires approval for this exact document revision.");
        List<Map<String,Object>> documents=new ArrayList<>();
        for (Map<String,Object> row:(List<Map<String,Object>>)snapshot.get("documents")) {
            Map<String,Object> document=new LinkedHashMap<>();
            for (String key:List.of("id","title","text","source")) document.put(key,row.get(key));
            documents.add(document);
        }
        if (!slots.tryAcquire()) throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"index_busy","Index service is busy; inspect status before trying again.");
        try {
            var payload=Map.of("snapshot_id",workspace+":"+revision,"documents",documents,"strategy",strategy);
            // Match Python's UTF-8 canonical document serialization (sorted object keys).
            var canonical=documents.stream().map(TreeMap::new).toList();
            String expectedHash=Requests.sha256(store.encode(canonical));
            var request=WorkerIdentity.apply(HttpRequest.newBuilder(URI.create(worker+"/api/rag/indexes/"+(build?"build":"status"))),serviceKey)
                .timeout(Duration.ofSeconds(build?100:10)).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofString(store.encode(payload))).build();
            var response=http.send(request,HttpResponse.BodyHandlers.ofString());
            if (response.statusCode()!=200) throw new ApiError(HttpStatus.BAD_GATEWAY,"index_worker_error","Index worker rejected the request; no automatic retry was made.");
            Map<String,Object> result=new LinkedHashMap<>(store.decode(response.body()));
            if (!Objects.equals(result.get("snapshot_id"),payload.get("snapshot_id"))
                    || !Objects.equals(result.get("documents_sha256"),expectedHash)
                    || !(result.get("status") instanceof String responseStatus)
                    || !Set.of("ready","missing","building","failed","cancelled","disabled","unavailable","capacity_exceeded").contains(responseStatus)
                    || ("ready".equals(result.get("status")) && !(result.get("index_id") instanceof String)))
                throw new ApiError(HttpStatus.BAD_GATEWAY,"index_identity_mismatch","Index response does not match the requested immutable snapshot.");
            result.put("workspace_id",workspace); result.put("revision",revision);
            long current=((Number)store.workspace(workspace,false).get("revision")).longValue();
            result.put("current_revision",current); result.put("current",current==revision);
            // A build may finish after an edit. The artifact is valid only for its original snapshot.
            if (current!=revision) result.put("workspace_index_status","stale");
            else result.put("workspace_index_status",result.get("status"));
            return result;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE,"index_interrupted","Index request interrupted; check its status before rebuilding.");
        } catch (java.io.IOException e) {
            throw new ApiError(HttpStatus.GATEWAY_TIMEOUT,"index_outcome_unknown","Index outcome is unknown; inspect status before rebuilding. Provider work may have occurred.");
        } finally { slots.release(); }
    }
}
