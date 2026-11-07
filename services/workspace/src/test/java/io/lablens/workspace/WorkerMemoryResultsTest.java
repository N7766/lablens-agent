package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;

class WorkerMemoryResultsTest {
    final WorkspaceStore.Claimed task=new WorkspaceStore.Claimed("t","w",7,"hash","agent","{}");
    Map<String,Object> context() {
        var memory=new LinkedHashMap<String,Object>();
        memory.put("id","m");memory.put("key","baseline");memory.put("version",1L);memory.put("created_by","u");memory.put("scope","workspace");memory.put("status","active");
        memory.put("source",Map.of("document_id","d","document_version",2L,"document_sha256","d".repeat(64),"snapshot_id","w:2","quote","中文 😀 \n"));
        memory.put("note","untrusted annotation");memory.put("expires_at",null);memory.put("created_at","2026-01-01T00:00:00Z");memory.put("updated_at","2026-01-01T00:00:00Z");
        return Map.of("workspace_id","w","revision",3L,"entries",List.of(memory));
    }
    Map<String,Object> metadata(Map<String,Object> context) throws Exception {
        String canonical=new ObjectMapper().configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS,true).writeValueAsString(context);
        var original=(Map<?,?>)((List<?>)context.get("entries")).getFirst();var originalSource=(Map<?,?>)original.get("source");
        var source=new LinkedHashMap<String,Object>();for(String k:List.of("document_id","document_version","document_sha256","snapshot_id"))source.put(k,originalSource.get(k));
        source.put("quote_sha256",Requests.sha256((String)originalSource.get("quote")));
        var entry=new LinkedHashMap<String,Object>();for(String k:List.of("id","key","version","created_by","scope","expires_at"))entry.put(k,original.get(k));entry.put("source",source);
        var metadata=new LinkedHashMap<String,Object>();metadata.put("version","workspace-memory-context-v1");metadata.put("implementation_sha256","a".repeat(64));
        metadata.put("workspace_id","w");metadata.put("revision",3L);metadata.put("context_sha256",Requests.sha256(canonical));metadata.put("serialized_utf8_bytes",canonical.getBytes(StandardCharsets.UTF_8).length);
        metadata.put("selected_count",1);metadata.put("authority","explicit_shared_member_hints_not_verified_evidence");metadata.put("automatic_memory",false);metadata.put("entries",List.of(entry));return metadata;
    }
    Map<String,Object> result(Map<String,Object> context) throws Exception {
        var result=new LinkedHashMap<String,Object>();result.put("mode","agent");result.put("status","answered");result.put("retrieval",Map.of("snapshot_id","w:7","strategy","bm25"));result.put("memory",metadata(context));result.put("usage",WorkerResults.zeroUsage(false));return result;
    }
    @Test void validUnicodeMemoryMatchesImmutableSnapshotAndMissingProvenanceFails() throws Exception {
        var context=context();var snapshot=Map.<String,Object>of("strategy","bm25","memory_context",context);var result=result(context);
        assertNotNull(WorkerResults.validate(result,task,snapshot));result.remove("memory");assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(result,task,snapshot));
    }
    @Test void changedQuoteHashRevisionContextOrAutomaticAuthorityIsRejected() throws Exception {
        var context=context();var snapshot=Map.<String,Object>of("strategy","bm25","memory_context",context);
        for(var change:Map.<String,Object>of("context_sha256","0".repeat(64),"workspace_id","other","revision",4L,"automatic_memory",true,"selected_count",2,"authority","verified").entrySet()) {
            var result=result(context);var metadata=metadata(context);metadata.put(change.getKey(),change.getValue());result.put("memory",metadata);
            assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(result,task,snapshot),change.getKey());
        }
        var fractional=result(context);var fractionalMetadata=metadata(context);fractionalMetadata.put("revision",3.5);fractional.put("memory",fractionalMetadata);
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(fractional,task,snapshot));
        var result=result(context);var metadata=metadata(context);var entry=new LinkedHashMap<>((Map<String,Object>)((List<?>)metadata.get("entries")).getFirst());
        var source=new LinkedHashMap<>((Map<String,Object>)entry.get("source"));source.put("quote_sha256","0".repeat(64));entry.put("source",source);metadata.put("entries",List.of(entry));result.put("memory",metadata);
        assertThrows(IllegalArgumentException.class,()->WorkerResults.validate(result,task,snapshot));
    }
    @Test void expiredMemoryFailureKeepsBoundedProvenanceAndV11TraceWithoutProviderPayload() throws Exception {
        var context=context();var snapshot=Map.<String,Object>of("strategy","bm25","memory_context",context);var result=result(context);result.put("status","invalid_memory_context");
        var metadata=metadata(context);metadata.put("raw_prompt","private payload");result.put("memory",metadata);
        result.put("agent",Map.of("version","workspace-agent-v11","snapshot_id","w:7","state","invalid_memory_context","trace",List.of(Map.of("event","stopped","state","invalid_memory_context"))));
        var saved=WorkerResults.safeError(Map.of("detail",result),task,snapshot);assertNotNull(saved);assertNotNull(saved.get("agent"));assertNotNull(saved.get("memory"));assertFalse(saved.toString().contains("private payload"));
        assertEquals("invalid_memory_context",saved.get("status"));assertEquals(false,((Map<?,?>)saved.get("usage")).get("provider_usage_unknown"));
    }
}
