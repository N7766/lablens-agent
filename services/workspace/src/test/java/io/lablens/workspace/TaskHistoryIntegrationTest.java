package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.transaction.support.TransactionTemplate;

/** Real DATETIME(6), InnoDB ordering, JSON projection and HTTP validation. No provider requests. */
@SpringBootTest(properties={"lablens.dispatch-enabled=false","lablens.mq.enabled=false"})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
class TaskHistoryIntegrationTest {
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;
    @Autowired TransactionTemplate transaction;
    private final List<String> workspaces=new ArrayList<>();
    private final String prefix=UUID.randomUUID().toString().substring(0,24);
    private int sequence;

    private String workspace() {
        String id=(String)store.createWorkspace(new Requests.Workspace("history-"+UUID.randomUUID())).get("id");
        workspaces.add(id); return id;
    }
    private String insert(String workspace,String createdAt) {
        String id=prefix+String.format("%012d",++sequence);
        db.update("INSERT INTO tasks(id,workspace_id,idempotency_key,request_hash,request_json,snapshot_revision,snapshot_json,snapshot_document_count,status,created_at,finished_at,execution_usage_json) VALUES (?,?,?,?,?,1,?,1,'SUCCEEDED',?,?,?)",
            id,workspace,"history-"+sequence,"a".repeat(64),store.encode(Map.of("question","Question "+sequence,"mode","extractive","strategy","bm25","top_k",5)),
            "{}",createdAt,createdAt,store.encode(WorkerResults.zeroUsage(false)));
        return id;
    }
    private Map<String,Object> page(String workspace,int limit,String cursor) {
        var params=new LinkedMultiValueMap<String,String>(); params.add("view","summary"); params.add("limit",Integer.toString(limit));
        if (cursor!=null) params.add("cursor",cursor);
        return store.taskSummaries(workspace,TaskHistoryQuery.parse(workspace,params));
    }
    @SuppressWarnings("unchecked") private List<Map<String,Object>> rows(Map<String,Object> page) { return (List<Map<String,Object>>)page.get("tasks"); }
    private List<String> ids(Map<String,Object> page) { return rows(page).stream().map(t->(String)t.get("id")).toList(); }
    @AfterEach void cleanup() {
        for (String id:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",id);
            db.update("DELETE FROM workspaces WHERE id=?",id);
        }
    }

    @Test void tiesAndMicrosecondBoundariesHaveStablePagesWithoutDuplicates() {
        String workspace=workspace();
        String a=insert(workspace,"2026-01-02 03:04:05.123455");
        String b=insert(workspace,"2026-01-02 03:04:05.123456");
        String c=insert(workspace,"2026-01-02 03:04:05.123456");
        String d=insert(workspace,"2026-01-02 03:04:05.123456");
        String e=insert(workspace,"2026-01-02 03:04:05.123457");
        var first=page(workspace,2,null); assertEquals(List.of(e,d),ids(first)); assertEquals(true,first.get("has_more"));
        var second=page(workspace,2,(String)first.get("next_cursor")); assertEquals(List.of(c,b),ids(second));
        var last=page(workspace,2,(String)second.get("next_cursor")); assertEquals(List.of(a),ids(last));
        assertEquals(false,last.get("has_more")); assertNull(last.get("next_cursor"));
    }

    @Test void deletingTheBoundaryRowDoesNotInvalidateTheNextPosition() {
        String workspace=workspace();
        String a=insert(workspace,"2026-01-02 03:04:05.123456"),b=insert(workspace,"2026-01-02 03:04:05.123456");
        String c=insert(workspace,"2026-01-02 03:04:05.123456"),d=insert(workspace,"2026-01-02 03:04:05.123456");
        var first=page(workspace,2,null); assertEquals(List.of(d,c),ids(first));
        db.update("DELETE FROM tasks WHERE id=?",c);
        assertEquals(List.of(b,a),ids(page(workspace,2,(String)first.get("next_cursor"))));
    }

    @Test void aConcurrentNewerInsertBetweenPagesDoesNotShiftOrRepeatTheEarlierPage() throws Exception {
        String workspace=workspace();
        String a=insert(workspace,"2026-01-02 03:04:05.123456"),b=insert(workspace,"2026-01-02 03:04:05.123456");
        String c=insert(workspace,"2026-01-02 03:04:05.123456");
        var inserted=new CountDownLatch(1); var commit=new CountDownLatch(1);
        try (var pool=Executors.newSingleThreadExecutor()) {
            var writer=pool.submit(()->transaction.execute(status->{
                String newest=insert(workspace,"2026-01-02 03:04:06.123456"); inserted.countDown();
                try { assertTrue(commit.await(5,TimeUnit.SECONDS)); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new IllegalStateException(error); }
                return newest;
            }));
            try {
                assertTrue(inserted.await(3,TimeUnit.SECONDS));
                var first=page(workspace,1,null); assertEquals(List.of(c),ids(first));
                commit.countDown(); String newest=writer.get(3,TimeUnit.SECONDS);
                assertEquals(List.of(b,a),ids(page(workspace,2,(String)first.get("next_cursor"))));
                assertEquals(List.of(newest,c),ids(page(workspace,2,null)));
            } finally { commit.countDown(); }
        }
    }

    @Test void cursorScopeCannotSwitchWorkspacesAndSummaryNeverIncludesForeignRows() throws Exception {
        String a=workspace(),b=workspace();
        insert(a,"2026-01-02 03:04:05.123456"); insert(a,"2026-01-02 03:04:05.123456");
        String other=insert(b,"2026-01-02 03:04:05.123456");
        String cursor=(String)page(a,1,null).get("next_cursor");
        assertEquals(400,assertThrows(ApiError.class,()->page(b,1,cursor)).status.value());
        assertEquals(List.of(other),ids(page(b,25,null)));
        mvc.perform(get("/api/workspaces/"+b+"/tasks").param("view","summary").param("cursor",cursor))
            .andExpect(status().isBadRequest()).andExpect(jsonPath("error").value("invalid_request"));
    }

    @Test void summaryOmitsLargeBodiesButDetailsKeepEvidenceErrorsAndUnknownUsage() throws Exception {
        String workspace=workspace(),id=insert(workspace,"2026-01-02 03:04:05.123456");
        String large="private-large-payload-".repeat(4000);
        Map<String,Object> result=Map.of("answer",large,"citations",List.of(Map.of("source","test://original-source","text","Exact source excerpt")));
        Map<String,Object> error=Map.of("status","agent_timeout","partial",large);
        db.update("UPDATE tasks SET snapshot_json=?,result_json=?,error_result_json=?,execution_usage_json=?,status='FAILED',usage_unknown=TRUE,error_code='agent_timeout',error_message='Safe timeout message' WHERE id=?",
            store.encode(Map.of("documents",large)),store.encode(result),store.encode(error),store.encode(WorkerResults.uncertainUsage(true)),id);
        var summary=rows(page(workspace,25,null)).getFirst();
        assertEquals("Question 1",summary.get("question")); assertEquals("FAILED",summary.get("status"));
        assertEquals(true,summary.get("usage_unknown")); assertEquals("agent_timeout",summary.get("error_code"));
        for (String excluded:List.of("request","snapshot_json","result","result_json","error_result","error_result_json","execution_usage","execution_usage_json")) assertFalse(summary.containsKey(excluded));
        assertFalse(store.encode(summary).contains("private-large-payload"));
        var detail=store.task(workspace,id);
        assertEquals(result,detail.get("result")); assertEquals(error,detail.get("error_result"));
        assertEquals(true,((Map<?,?>)detail.get("execution_usage")).get("provider_usage_unknown"));
        assertNull(((Map<?,?>)detail.get("execution_usage")).get("model_calls"));
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks")).andExpect(status().isOk())
            .andExpect(jsonPath("tasks[0].result.citations[0].source").value("test://original-source"))
            .andExpect(jsonPath("view").doesNotExist());
    }

    @Test void emptyAndExactSizedPagesDoNotInventAnotherPage() throws Exception {
        String workspace=workspace();
        var empty=page(workspace,25,null); assertTrue(rows(empty).isEmpty()); assertNull(empty.get("next_cursor")); assertEquals(false,empty.get("has_more"));
        insert(workspace,"2026-01-02 03:04:05.123456"); insert(workspace,"2026-01-02 03:04:05.123456");
        var exact=page(workspace,2,null); assertEquals(2,rows(exact).size()); assertEquals(false,exact.get("has_more"));
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","summary"))
            .andExpect(status().isOk()).andExpect(jsonPath("limit").value(25)).andExpect(jsonPath("ordering").value("created_at_desc_id_desc"));
    }

    @Test void invalidPaginationParametersReturn400WithoutSilentlyFallingBackToFullHistory() throws Exception {
        String workspace=workspace();
        for (String limit:List.of("0","101","-1","1.5","025","","999999999999"))
            mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","summary").param("limit",limit)).andExpect(status().isBadRequest());
        for (String cursor:List.of("","v2.abc","v1.a","v1."+"a".repeat(193),"v1.a="))
            mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","summary").param("cursor",cursor)).andExpect(status().isBadRequest());
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","full")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("limit","25")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","summary").param("limit","1","2")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/workspaces/"+workspace+"/tasks").param("view","summary").param("unknown","x")).andExpect(status().isBadRequest());
    }
}
