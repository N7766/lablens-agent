package io.lablens.workspace;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;

/** Exact attributed USER text, not LLM-generated facts. Overflow rejects admission. */
final class ConversationSummary {
    static final int MAX_MESSAGES=100;
    static final int MAX_BYTES=16*1024;
    static final String VERSION="conversation-summary-v1";
    static final String AUTHORITY="exact_previous_user_messages_not_verified_evidence";
    private ConversationSummary() {}

    /** Called under the conversation lock, before revision/task/outbox mutation. */
    static Map<String,Object> snapshot(JdbcTemplate db,String workspace,String conversation,long beforeSeq,long expected) {
        if (expected==0) return null;
        if (expected<0 || expected>MAX_MESSAGES) throw overflow();
        var rows=db.query("SELECT conversation_seq,snapshot_revision,JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.question')) AS question "
            +"FROM tasks WHERE workspace_id=? AND conversation_id=? AND conversation_seq<? "
            +"AND status IN ('SUCCEEDED','FAILED','CANCELLED') ORDER BY conversation_seq ASC LIMIT ?",(rs,n)->
                Map.<String,Object>of("seq",rs.getLong("conversation_seq"),"question",rs.getString("question"),
                    "snapshot_id",workspace+":"+rs.getLong("snapshot_revision")),workspace,conversation,beforeSeq,MAX_MESSAGES+1);
        // A missing row cannot be reconstructed by guessing from assistant answers.
        if (rows.size()!=expected) throw overflow();
        return create(rows);
    }

    static Map<String,Object> create(List<Map<String,Object>> rows) {
        if (rows.isEmpty()) return null;
        if (rows.size()>MAX_MESSAGES) throw overflow();
        var messages=new ArrayList<Map<String,Object>>();
        long previous=0;
        for (var row:rows) {
            if (!(row.get("seq") instanceof Number number) || !(row.get("question") instanceof String question)
                || !(row.get("snapshot_id") instanceof String snapshot)) throw overflow();
            long seq=number.longValue();
            if (seq<=previous || question.isEmpty() || question.length()>2000 || snapshot.isEmpty() || snapshot.length()>200
                || !StandardCharsets.UTF_8.newEncoder().canEncode(question) || !StandardCharsets.UTF_8.newEncoder().canEncode(snapshot)) throw overflow();
            messages.add(Map.of("seq",seq,"question",question,"snapshot_id",snapshot,"question_sha256",Requests.sha256(question)));
            previous=seq;
        }
        Map<String,Object> result=Map.of("version",VERSION,"authority",AUTHORITY,"messages",messages,
            "source_range",Map.of("first_seq",messages.getFirst().get("seq"),"last_seq",messages.getLast().get("seq")),
            "source_sha256",Requests.sha256(MemoryRequests.canonical(messages)),"omitted_assistant_answers",messages.size());
        if (MemoryRequests.canonical(result).getBytes(StandardCharsets.UTF_8).length>MAX_BYTES) throw overflow();
        return result;
    }

    /** The final review retains two full turns; preflight its exact user summary. */
    @SuppressWarnings("unchecked")
    static void ensureReviewCapacity(Map<String,Object> summary,List<Map<String,Object>> recent) {
        var rows=new ArrayList<Map<String,Object>>();
        if (summary!=null) rows.addAll((List<Map<String,Object>>)summary.get("messages"));
        rows.addAll(recent.subList(0,Math.max(0,recent.size()-2)));
        create(rows);
    }

    private static ApiError overflow() {
        return new ApiError(HttpStatus.CONFLICT,"context_history_requires_new_conversation",
            "Exact user history exceeds the bounded summary or has missing records. Start a new conversation and explicitly restate the goal, constraints and unresolved questions. No new turn or provider request was created.");
    }
}
