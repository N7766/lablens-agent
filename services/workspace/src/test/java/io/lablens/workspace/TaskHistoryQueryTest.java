package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.util.LinkedMultiValueMap;

class TaskHistoryQueryTest {
    private final String workspace=UUID.randomUUID().toString(),id=UUID.randomUUID().toString();
    private TaskHistoryQuery parse(String token) {
        var params=new LinkedMultiValueMap<String,String>();params.add("view","summary");params.add("cursor",token);
        return TaskHistoryQuery.parse(workspace,params);
    }
    @Test void positionRoundTripsMicrosecondsAndWorkspaceBindingWithoutARowLookup() {
        String time="2026-01-02T03:04:05.123456Z";
        var parsed=parse(TaskHistoryQuery.encode(workspace,time,id));
        assertEquals(25,parsed.limit());assertEquals(Instant.parse(time),parsed.cursor().createdAt());assertEquals(id,parsed.cursor().id());
    }
    @Test void cursorRejectsNonCanonicalPrecisionInvalidDatesAndAmbiguousIdentifiers() {
        for (String time:List.of("2026-01-02T03:04:05.1234567Z","2026-01-02T03:04:05.123000Z","2026-02-30T03:04:05Z","0999-01-01T00:00:00Z","2026-01-02T03:04:05+00:00"))
            assertEquals(400,assertThrows(ApiError.class,()->parse(TaskHistoryQuery.encode(workspace,time,id))).status.value());
        assertThrows(ApiError.class,()->parse(TaskHistoryQuery.encode(workspace,"2026-01-02T03:04:05Z","1-1-1-1-1")));
        assertThrows(ApiError.class,()->parse(TaskHistoryQuery.encode(UUID.randomUUID().toString(),"2026-01-02T03:04:05Z",id)));
    }
    @Test void aLegacyQueryRequiresNoPaginationParametersAndNewLimitIsBounded() {
        var params=new LinkedMultiValueMap<String,String>();assertNull(TaskHistoryQuery.parse(workspace,params));
        params.add("view","summary");params.add("limit","100");assertEquals(100,TaskHistoryQuery.parse(workspace,params).limit());
        params.set("limit","101");assertThrows(ApiError.class,()->TaskHistoryQuery.parse(workspace,params));
    }
}
