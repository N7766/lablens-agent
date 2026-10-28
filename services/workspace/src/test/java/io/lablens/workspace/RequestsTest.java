package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

class RequestsTest {
    @Test void canonicalDefaultsAndExplicitValuesAreEquivalent() {
        assertEquals(new Requests.Task("question","hybrid","extractive",5),new Requests.Task(" question ",null,null,null).normalized());
        assertEquals(new Requests.Task("question","bm25","live",1),new Requests.Task("question","bm25","live",1).normalized());
    }
    @Test void unknownStrategiesCannotSilentlyChooseAnotherBackend() {
        assertThrows(ApiError.class,()->new Requests.Task("q","vector",null,5).normalized());
        assertThrows(ApiError.class,()->new Requests.Task("q",null,"remote",5).normalized());
    }
    @Test void boundedTopKAndQuestionAreEnforced() {
        assertThrows(ApiError.class,()->new Requests.Task("q",null,null,0).normalized());
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
}
