package io.lablens.workspace;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Requests {
    private Requests() {}
    public record Workspace(@NotBlank @Size(max=160) String name) {}
    public record Document(@NotBlank @Size(max=300) String title, @NotBlank @Size(max=20000) String text, @Size(max=1000) String source) {}
    public record State(String goal,List<String> constraints,String focus) {
        public State normalized() {
            if (goal!=null && goal.length()>2000) throw ApiError.bad("goal must contain at most 2000 characters");
            if (focus!=null && focus.length()>300) throw ApiError.bad("focus must contain at most 300 characters");
            if (constraints!=null && (constraints.size()>12 || constraints.stream().anyMatch(s->s==null || s.isBlank() || s.length()>300)))
                throw ApiError.bad("constraints must contain at most 12 nonblank strings of at most 300 characters");
            return new State(goal==null?null:goal.strip(),constraints==null?null:constraints.stream().map(String::strip).toList(),focus==null?null:focus.strip());
        }
        public Map<String,Object> fields() {
            Map<String,Object> fields=new LinkedHashMap<>();
            if (goal!=null) fields.put("goal",goal);
            if (constraints!=null) fields.put("constraints",constraints);
            if (focus!=null) fields.put("focus",focus);
            return fields;
        }
        Map<String,Object> initial() {
            Map<String,Object> result=new LinkedHashMap<>(); result.put("goal",""); result.put("constraints",List.of()); result.put("focus","");
            result.putAll(normalized().fields()); return result;
        }
    }
    public record Conversation(String title,String goal,List<String> constraints,String focus) {
        public Conversation normalized() {
            if (title!=null && title.length()>160) throw ApiError.bad("title must contain at most 160 characters");
            State state=new State(goal,constraints,focus).normalized();
            return new Conversation(title==null || title.isBlank()?"New conversation":title.strip(),state.goal(),state.constraints(),state.focus());
        }
        State state() { return new State(goal,constraints,focus); }
    }
    public record Turn(String question,String strategy,String mode,@JsonProperty("top_k") Integer topK,
                       @JsonProperty("approved_revision") Long approvedRevision,
                       @JsonProperty("expected_conversation_revision") Long expectedConversationRevision,State state) {
        public Turn normalized() {
            Task task=new Task(question,strategy==null?"bm25":strategy,mode==null?"agent":mode,topK,approvedRevision).normalized();
            if (!"agent".equals(task.mode())) throw ApiError.bad("Conversation turns currently require mode=agent");
            if (expectedConversationRevision==null || expectedConversationRevision<0) throw ApiError.bad("expected_conversation_revision must be a nonnegative integer");
            return new Turn(task.question(),task.strategy(),task.mode(),task.topK(),task.approvedRevision(),expectedConversationRevision,state==null?null:state.normalized());
        }
        Task task() { return new Task(question,strategy,mode,topK,approvedRevision); }
        Map<String,Object> fields(String conversation) {
            Map<String,Object> fields=new LinkedHashMap<>(task().fields());
            fields.put("conversation_id",conversation); fields.put("expected_conversation_revision",expectedConversationRevision);
            if (state!=null) fields.put("state",state.fields());
            return fields;
        }
    }
    public record Task(@NotBlank @Size(max=2000) String question, String strategy, String mode, @JsonProperty("top_k") Integer topK,
                       @JsonProperty("approved_revision") Long approvedRevision) {
        public Task(String question,String strategy,String mode,Integer topK) { this(question,strategy,mode,topK,null); }
        public boolean requiresApproval() { return !"extractive".equals(mode) || !"bm25".equals(strategy); }
        public Task normalized() {
            String s=strategy==null ? "hybrid" : strategy;
            String m=mode==null ? "extractive" : mode;
            int k=topK==null ? 5 : topK;
            if (!java.util.Set.of("bm25","dense","hybrid").contains(s)) throw ApiError.bad("strategy must be bm25, dense or hybrid");
            if (!java.util.Set.of("extractive","live","agent").contains(m)) throw ApiError.bad("mode must be extractive, live or agent");
            if (k<1 || k>10) throw ApiError.bad("top_k must be between 1 and 10");
            if (question==null || question.isBlank() || question.length()>2000) throw ApiError.bad("question must contain 1–2000 characters");
            if (approvedRevision!=null && approvedRevision<0) throw ApiError.bad("approved_revision must be nonnegative");
            return new Task(question.strip(),s,m,k,approvedRevision);
        }
        public Map<String,Object> fields() {
            Map<String,Object> result=new LinkedHashMap<>();
            result.put("question",question); result.put("strategy",strategy); result.put("mode",mode); result.put("top_k",topK);
            if (approvedRevision!=null) result.put("approved_revision",approvedRevision);
            return result;
        }
    }
    static String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    static void documentId(String id) {
        if (id==null || !id.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")) throw ApiError.bad("Document IDs must contain 1–128 URL-safe ASCII letters, digits, dots, underscores, colons or hyphens");
    }
    static void idempotencyKey(String key) {
        if (key==null || !key.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")) throw ApiError.bad("Idempotency-Key must contain 1–128 URL-safe ASCII characters");
    }
}
