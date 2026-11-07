package io.lablens.workspace;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.*;

final class MemoryRequests {
    // Python json.dumps(ensure_ascii=False, sort_keys=True) uses lowercase hex escapes.
    private static final com.fasterxml.jackson.databind.ObjectMapper CANONICAL=new com.fasterxml.jackson.databind.ObjectMapper(
        com.fasterxml.jackson.core.JsonFactory.builder().disable(com.fasterxml.jackson.core.json.JsonWriteFeature.WRITE_HEX_UPPER_CASE).build())
        .configure(com.fasterxml.jackson.databind.SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS,true);
    private MemoryRequests() {}
    static String canonical(Object value) {
        try { return CANONICAL.writeValueAsString(value); }
        catch (com.fasterxml.jackson.core.JsonProcessingException e) { throw new IllegalArgumentException("Invalid memory snapshot",e); }
    }
    record Mutation(String key,@JsonProperty("document_id") String documentId,
                    @JsonProperty("document_version") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long documentVersion,
                    String quote,String note,@JsonProperty("expires_at") String expiresAt,
                    @JsonProperty("expected_revision") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long expectedRevision,
                    @JsonProperty("expected_version") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long expectedVersion) {
        Mutation normalized(boolean update) {
            revision(expectedRevision,"expected_revision",0);
            if (update) revision(expectedVersion,"expected_version",1);
            else if (expectedVersion!=null) throw ApiError.bad("expected_version is only used for updates");
            String normalizedKey=key==null?null:key.strip();
            if (normalizedKey==null || !normalizedKey.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")) throw ApiError.bad("key must contain 1–80 URL-safe ASCII characters");
            Requests.documentId(documentId); revision(documentVersion,"document_version",1);
            text(quote,"quote",1000,false); text(note,"note",500,true);
            if (expiresAt!=null) {
                try { Instant.parse(expiresAt); } catch (DateTimeParseException e) { throw ApiError.bad("expires_at must be an ISO-8601 instant"); }
            }
            return new Mutation(normalizedKey,documentId,documentVersion,quote,note==null?"":note,expiresAt,expectedRevision,expectedVersion);
        }
    }
    record Delete(@JsonProperty("expected_revision") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long expectedRevision,
                  @JsonProperty("expected_version") @JsonDeserialize(using=BulkImportRequests.RevisionDeserializer.class) Long expectedVersion) {
        void validate() { revision(expectedRevision,"expected_revision",0); revision(expectedVersion,"expected_version",1); }
    }
    static void revision(Long value,String name,long minimum) {
        if (value==null || value<minimum) throw ApiError.bad(name+" must be an integer >= "+minimum);
    }
    static void id(String value) {
        if (value==null || !value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) throw ApiError.bad("memory id must be a canonical UUID");
    }
    static List<String> selection(List<String> ids,Long approval,String mode) {
        if (ids==null || ids.isEmpty()) {
            if (approval!=null) throw ApiError.bad("approved_memory_revision requires a nonempty memory_ids selection");
            return List.of();
        }
        if (!"agent".equals(mode)) throw ApiError.bad("Memory selection requires mode=agent");
        if (ids.size()>8) throw ApiError.bad("Select at most 8 memories");
        for (String value:ids) id(value);
        if (new HashSet<>(ids).size()!=ids.size()) throw ApiError.bad("memory_ids must be unique");
        if (approval!=null) revision(approval,"approved_memory_revision",0);
        return List.copyOf(ids);
    }
    private static void text(String value,String name,int max,boolean optional) {
        if (value==null && optional) return;
        if (value==null || value.length()>max || (!optional && value.isBlank())) throw ApiError.bad(name+" exceeds its text bounds");
        for (int i=0;i<value.length();i++) {
            char c=value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i>=value.length() || !Character.isLowSurrogate(value.charAt(i))) throw ApiError.bad(name+" must contain valid Unicode");
            } else if (Character.isLowSurrogate(c)) throw ApiError.bad(name+" must contain valid Unicode");
        }
    }
}
