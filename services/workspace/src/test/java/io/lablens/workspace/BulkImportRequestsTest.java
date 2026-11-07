package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class BulkImportRequestsTest {
    private BulkImportRequests.Document document(String id,String text) { return new BulkImportRequests.Document(id,"Title",text,null); }
    @Test void normalizesSourceWithoutChangingBodyOrOrderedDocumentIdentity() {
        var batch=new BulkImportRequests.Batch(List.of(document("b"," 文档 \n"),document("a","😀")),0L).normalized();
        assertEquals("document:b",batch.documents().getFirst().source());
        assertEquals(" 文档 \n",batch.documents().getFirst().text());
        assertEquals(List.of("documents","expected_revision"),new ArrayList<>(batch.fields().keySet()));
        assertEquals(List.of("id","title","text","source"),new ArrayList<>(batch.documents().getFirst().fields().keySet()));
    }
    @Test void rejectsMissingRevisionDuplicatesAndInvalidIds() {
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("a","x")),null).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("a","x")),-1L).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("a","x"),document("a","y")),0L).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("bad/id","x")),0L).normalized());
    }
    @Test void enforcesIndependentDocumentAndBatchBounds() {
        var documents=new ArrayList<BulkImportRequests.Document>();
        for (int i=0;i<25;i++) documents.add(document("d"+i,"x"));
        assertEquals(25,new BulkImportRequests.Batch(documents,0L).normalized().documents().size());
        documents.add(document("extra","x"));
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(documents,0L).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(),0L).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("a","x".repeat(20001))),0L).normalized());
        var large=new ArrayList<BulkImportRequests.Document>();
        for (int i=0;i<5;i++) large.add(document("d"+i,"x".repeat(20000)));
        assertEquals(5,new BulkImportRequests.Batch(large,0L).normalized().documents().size());
        large.add(document("extra","x"));
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(large,0L).normalized());
    }
    @Test void rejectsNullBlankMalformedUnicodeAndOversizedMetadata() {
        var nullEntry=new ArrayList<BulkImportRequests.Document>(); nullEntry.add(null);
        assertThrows(ApiError.class,()->new BulkImportRequests.Batch(nullEntry,0L).normalized());
        for (String text:new String[]{" ","\ud800","\udc00","\ud800x"})
            assertThrows(ApiError.class,()->new BulkImportRequests.Batch(List.of(document("a",text)),0L).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Document("a","x".repeat(301),"body",null).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Document("a","Title","body","x".repeat(1001)).normalized());
        assertThrows(ApiError.class,()->new BulkImportRequests.Document("a","Title","body","\ud800").normalized());
    }
}
