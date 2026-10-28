package io.lablens.workspace;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;

public final class Requests {
    private Requests() {}
    public record Workspace(@NotBlank @Size(max=160) String name) {}
    public record Document(@NotBlank @Size(max=300) String title, @NotBlank @Size(max=20000) String text, @Size(max=1000) String source) {}
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
