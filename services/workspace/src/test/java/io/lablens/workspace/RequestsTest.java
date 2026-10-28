package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.Map;
import org.junit.jupiter.api.Test;

class RequestsTest {
    @Test void canonicalDefaultsAndExplicitValuesAreEquivalent() {
        assertEquals(new Requests.Task("question","hybrid","extractive",5),new Requests.Task(" question ",null,null,null).normalized());
        assertEquals(new Requests.Task("question","bm25","live",1),new Requests.Task("question","bm25","live",1).normalized());
        assertEquals(new Requests.Task("question","bm25","agent",1),new Requests.Task("question","bm25","agent",1).normalized());
    }
    @Test void unknownStrategiesCannotSilentlyChooseAnotherBackend() {
        assertThrows(ApiError.class,()->new Requests.Task("q","vector",null,5).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("q",null,"remote",5).normalized());
    }
    @Test void boundedTopKAndQuestionAreEnforced() {
        assertThrows(ApiError.class,()->new Requests.Task("q",null,null,0).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("q","bm25","agent",1,-1L).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("q",null,null,11).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("  ",null,null,1).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("x".repeat(2001),null,null,1).normalized());
    }
    @Test void identifiersCannotIntroducePathOrHeaderSyntax() {
        for (String id:new String[]{"","../secret","x/y","x\nY","x".repeat(129)}) assertThrows(ApiError.class,()->Requests.documentId(id));
        for (String id:new String[]{"","x y","x\rY","x".repeat(129)}) assertThrows(ApiError.class,()->Requests.idempotencyKey(id));
        Requests.documentId("run-1.md"); Requests.documentId("note:units"); Requests.idempotencyKey("load:run-1");
    }
    @Test void sha256HasStableKnownDigest() {
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",Requests.sha256("abc"));
    }
    @Test void conversationStateAndTurnInputsHaveExplicitBoundsAndPatchSemantics() {
        assertEquals(Map.of(),new Requests.State(null,null,null).normalized().fields());
        assertEquals(Map.of("constraints",java.util.List.of()),new Requests.State(null,java.util.List.of(),null).normalized().fields());
        assertThrows(ApiError.class,()->new Requests.State("x".repeat(2001),null,null).normalized());
        assertThrows(ApiError.class,()->new Requests.State(null,java.util.Collections.nCopies(13,"constraint"),null).normalized());
        assertThrows(ApiError.class,()->new Requests.State(null,java.util.List.of(" "),null).normalized());
        assertThrows(ApiError.class,()->new Requests.State(null,null,"x".repeat(301)).normalized());
        assertThrows(ApiError.class,()->new Requests.Turn("q",null,"extractive",3,1L,0L,null).normalized());
        assertThrows(ApiError.class,()->new Requests.Turn("q",null,null,3,1L,null,null).normalized());
        assertEquals("agent",new Requests.Turn("q",null,null,3,1L,0L,null).normalized().mode());
    }
}
