package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.*;
import org.junit.jupiter.api.Test;

class MemoryRequestsTest {
    private MemoryRequests.Mutation value(String quote,String note,Long revision,Long version) {
        return new MemoryRequests.Mutation(" source-key ","d",1L,quote,note,null,revision,version);
    }
    @Test void preservesSourceAndNoteBytesWhileNormalizingKey() {
        var v=value(" 中文 😀 \n"," note \n",0L,null).normalized(false);
        assertEquals("source-key",v.key()); assertEquals(" 中文 😀 \n",v.quote()); assertEquals(" note \n",v.note());
    }
    @Test void rejectsMalformedUnicodeEmptyQuotesOversizeAndInvalidRevisions() {
        for (String quote:new String[]{" ","x".repeat(1001),"\ud800","\udc00"}) assertThrows(ApiError.class,()->value(quote,"",0L,null).normalized(false));
        assertThrows(ApiError.class,()->value("q","n".repeat(501),0L,null).normalized(false));
        assertThrows(ApiError.class,()->value("q","\ud800",0L,null).normalized(false));
        assertThrows(ApiError.class,()->value("q","",null,null).normalized(false));
        assertThrows(ApiError.class,()->value("q","",-1L,null).normalized(false));
        assertThrows(ApiError.class,()->value("q","",0L,1L).normalized(false));
        assertThrows(ApiError.class,()->value("q","",0L,null).normalized(true));
    }
    @Test void expiryRequiresOffsetAndDeleteRequiresBothVersions() {
        assertThrows(ApiError.class,()->new MemoryRequests.Mutation("key","d",1L,"q",null,"2026-01-01",0L,null).normalized(false));
        assertThrows(ApiError.class,()->new MemoryRequests.Delete(1L,null).validate());
        assertThrows(ApiError.class,()->new MemoryRequests.Delete(null,1L).validate());
        new MemoryRequests.Delete(0L,1L).validate();
    }
    @Test void rejectsFloatingStringAndBooleanMemoryVersionsAtJsonBoundary() {
        var json=new ObjectMapper();
        for (String invalid:List.of("1.0","\"1\"","true")) {
            assertThrows(Exception.class,()->json.readValue("{\"expected_revision\":"+invalid+",\"expected_version\":1}",MemoryRequests.Delete.class));
            assertThrows(Exception.class,()->json.readValue("{\"approved_memory_revision\":"+invalid+"}",Requests.Task.class));
        }
    }
    @Test void selectedMemoryMustBeBoundedUniqueCanonicalAndAgentOnly() {
        String id=UUID.randomUUID().toString();
        assertThrows(ApiError.class,()->MemoryRequests.selection(List.of(id,id),0L,"agent"));
        assertThrows(ApiError.class,()->MemoryRequests.selection(List.of("invalid"),0L,"agent"));
        assertThrows(ApiError.class,()->MemoryRequests.selection(List.of(id),0L,"live"));
        assertThrows(ApiError.class,()->MemoryRequests.selection(List.of(),0L,"agent"));
        assertThrows(ApiError.class,()->MemoryRequests.selection(Collections.nCopies(9,id),0L,"agent"));
        assertEquals(List.of(id),MemoryRequests.selection(List.of(id),null,"agent")); // Missing approval is a durable admission conflict.
    }
    @Test void oldTaskAndTurnFieldsRemainByteCompatibleWhenMemoryOmitted() {
        assertEquals(new Requests.Task("q","bm25","agent",3,1L),new Requests.Task("q","bm25","agent",3,1L,List.of(),null).normalized());
        assertEquals(List.of("question","strategy","mode","top_k","approved_revision"),new ArrayList<>(new Requests.Task("q","bm25","agent",3,1L).normalized().fields().keySet()));
        String id=UUID.randomUUID().toString();
        var turn=new Requests.Turn("q","bm25","agent",3,1L,0L,null,List.of(id),2L).normalized();
        assertEquals(List.of(id),turn.task().memoryIds()); assertEquals(2L,turn.fields("conversation").get("approved_memory_revision"));
    }
    @Test void canonicalHashMatchesPythonForControlCharactersUnicodeAndEscapes() {
        String encoded=MemoryRequests.canonical(Map.of("z","中文 😀 \n\u000f\\\"","a",Map.of("n",1,"empty","")));
        assertEquals("{\"a\":{\"empty\":\"\",\"n\":1},\"z\":\"中文 😀 \\n\\u000f\\\\\\\"\"}",encoded);
    }
}
