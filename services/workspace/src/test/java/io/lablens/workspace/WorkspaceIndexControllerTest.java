package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class WorkspaceIndexControllerTest {
    @Test void fencesRemoteCostsAndMarksEditDuringBuildStale() throws Exception {
        WorkspaceStore store=mock(WorkspaceStore.class);
        when(store.documents("w")).thenReturn(Map.of("revision",4L,"documents",List.of(Map.of("id","d","title","Title","text","body","source","test://d","version",4L))));
        when(store.workspace("w",false)).thenReturn(Map.of("revision",5L));
        when(store.encode(any())).thenAnswer(i->new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(i.getArgument(0)));
        var document=new TreeMap<String,Object>(Map.of("id","d","title","Title","text","body","source","test://d"));
        String hash=Requests.sha256(new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(List.of(document)));
        when(store.decode(anyString())).thenReturn(Map.of("status","ready","index_id","fixed","snapshot_id","w:4","documents_sha256",hash));
        var calls=new AtomicInteger();
        String serviceKey="round16-index-fixture-key-123456789";
        var serviceHeader=new java.util.concurrent.atomic.AtomicReference<String>();
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/rag/indexes/build",exchange->{
            serviceHeader.set(exchange.getRequestHeaders().getFirst("X-LabLens-Service-Key"));
            calls.incrementAndGet();
            String body=new String(exchange.getRequestBody().readAllBytes(),StandardCharsets.UTF_8);
            assertTrue(body.contains("w:4")); assertFalse(body.contains("\"version\""));
            byte[] response="{\"status\":\"ready\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200,response.length); exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        try {
            var controller=new WorkspaceIndexController(store,"http://127.0.0.1:"+server.getAddress().getPort());
            org.springframework.test.util.ReflectionTestUtils.setField(controller,"serviceKey",serviceKey);
            var conflict=assertThrows(ApiError.class,()->controller.build("w",new WorkspaceIndexController.Input("dense",3L,3L)));
            assertEquals("revision_conflict",conflict.code);
            var approval=assertThrows(ApiError.class,()->controller.build("w",new WorkspaceIndexController.Input("dense",4L,null)));
            assertEquals("provider_approval_required",approval.code); assertEquals(0,calls.get());
            Map<?,?> result=(Map<?,?>)controller.build("w",new WorkspaceIndexController.Input("bm25",4L,null));
            assertEquals(false,result.get("current")); assertEquals("stale",result.get("workspace_index_status"));
            assertEquals(1,calls.get());
            assertEquals(serviceKey,serviceHeader.get());
            when(store.decode(anyString())).thenReturn(Map.of("status","ready","index_id","wrong","snapshot_id","another:4","documents_sha256",hash));
            var mismatch=assertThrows(ApiError.class,()->controller.build("w",new WorkspaceIndexController.Input("bm25",4L,null)));
            assertEquals("index_identity_mismatch",mismatch.code);
        } finally { server.stop(0); }
    }
}
