package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.Test;

class ConversationSummaryTest {
    private Map<String,Object> message(long seq,String question,String snapshot) {
        return Map.of("seq",seq,"question",question,"snapshot_id",snapshot,"answer","Unverified assistant assertion must never survive");
    }
    @Test void exactUserSummaryMatchesPythonCanonicalHashIncludingUnicodeAndControls() {
        var result=ConversationSummary.create(List.of(message(1," 中文 😀 \n\u000f\\\"不要将验证集称为测试集。 ","workspace:1"),
            message(2,"更正：保留 SFT 基线；仍需查证 DPO 下降原因。","workspace:2")));
        assertEquals("ed3c60612dbf4928bf4ce346c77c6e46580ef8f0ed6a1afe09b70daf0df27386",result.get("source_sha256"));
        assertEquals(ConversationSummary.AUTHORITY,result.get("authority"));
        assertEquals(2,result.get("omitted_assistant_answers"));
        var messages=(List<?>)result.get("messages");
        assertFalse(((Map<?,?>)messages.getFirst()).containsKey("answer"));
        assertEquals(Map.of("first_seq",1L,"last_seq",2L),result.get("source_range"));
    }
    @Test void boundedSummaryStopsInsteadOfTruncatingCorrectionsOrInferringFacts() {
        var many=new ArrayList<Map<String,Object>>();
        for (int i=1;i<=101;i++) many.add(message(i,"No production traffic","w:1"));
        var overflow=assertThrows(ApiError.class,()->ConversationSummary.create(many));
        assertEquals("context_history_requires_new_conversation",overflow.code);
        assertEquals(409,overflow.status.value());
        var large=new ArrayList<Map<String,Object>>();
        for (int i=1;i<=4;i++) large.add(message(i,"不".repeat(2000),"w:1"));
        assertThrows(ApiError.class,()->ConversationSummary.create(large));
        assertNull(ConversationSummary.create(List.of()));
    }
    @Test void malformedOrderTextOrProvenanceCannotProduceAHistoricalSummary() {
        assertThrows(ApiError.class,()->ConversationSummary.create(List.of(message(2,"q","w:1"),message(1,"q","w:1"))));
        assertThrows(ApiError.class,()->ConversationSummary.create(List.of(message(1,"q","w:1"),message(1,"q","w:1"))));
        assertThrows(ApiError.class,()->ConversationSummary.create(List.of(message(1,"\ud800","w:1"))));
        assertThrows(ApiError.class,()->ConversationSummary.create(List.of(message(1,"q",""))));
    }
}
