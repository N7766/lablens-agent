package io.lablens.workspace;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import java.io.IOException;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Limits use Java UTF-16 characters, consistently with single-document writes. */
public final class BulkImportRequests {
    private BulkImportRequests() {}
    static final int MAX_DOCUMENTS=25;
    static final int MAX_CHARACTERS=100000;
    static final int MAX_REQUEST_BYTES=1048576;

    public record Document(String id,String title,String text,String source) {
        Document normalized() {
            Requests.documentId(id);
            textField(title,"title",300,false);
            textField(text,"text",20000,false);
            textField(source,"source",1000,true);
            return new Document(id,title,text,WorkspaceStore.sourceLabel(source,id));
        }
        Map<String,Object> fields() {
            Map<String,Object> fields=new LinkedHashMap<>();
            fields.put("id",id); fields.put("title",title); fields.put("text",text); fields.put("source",source); return fields;
        }
    }

    public record Batch(List<Document> documents,@JsonProperty("expected_revision") @JsonDeserialize(using=RevisionDeserializer.class) Long expectedRevision) {
        Batch normalized() {
            if (expectedRevision==null || expectedRevision<0) throw ApiError.bad("expected_revision must be a nonnegative integer");
            if (documents==null || documents.isEmpty() || documents.size()>MAX_DOCUMENTS)
                throw ApiError.bad("A batch must contain 1–25 documents");
            var ids=new HashSet<String>();
            int characters=0;
            for (Document document:documents) {
                if (document==null) throw ApiError.bad("documents must not contain null entries");
                document.normalized();
                if (!ids.add(document.id())) throw ApiError.bad("Document IDs must be unique within a batch");
                characters+=document.text().length();
            }
            if (characters>MAX_CHARACTERS) throw ApiError.bad("A batch allows at most 100000 text characters");
            return new Batch(documents.stream().map(Document::normalized).toList(),expectedRevision);
        }
        Map<String,Object> fields() {
            Map<String,Object> fields=new LinkedHashMap<>();
            fields.put("documents",documents.stream().map(Document::fields).toList()); fields.put("expected_revision",expectedRevision); return fields;
        }
    }

    public static class RevisionDeserializer extends JsonDeserializer<Long> {
        @Override public Long deserialize(JsonParser parser,DeserializationContext context) throws IOException {
            if (parser.currentToken()!=JsonToken.VALUE_NUMBER_INT) return (Long)context.handleUnexpectedToken(Long.class,parser);
            return parser.getLongValue();
        }
    }

    private static void textField(String value,String field,int max,boolean optional) {
        if (value==null) { if (optional) return; throw ApiError.bad(field+" must not be null"); }
        if (value.length()>max || (!optional && value.isBlank())) throw ApiError.bad(field+" must contain "+(optional?"0":"1")+"–"+max+" characters");
        for (int i=0;i<value.length();i++) {
            char c=value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i>=value.length() || !Character.isLowSurrogate(value.charAt(i))) throw ApiError.bad(field+" must contain valid Unicode");
            } else if (Character.isLowSurrogate(c)) throw ApiError.bad(field+" must contain valid Unicode");
        }
    }
}
