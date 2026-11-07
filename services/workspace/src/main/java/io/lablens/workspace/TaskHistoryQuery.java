package io.lablens.workspace;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.Set;
import java.util.UUID;
import org.springframework.util.MultiValueMap;

/** A bounded, workspace-bound position token. It is neither an authorization token nor a snapshot. */
public record TaskHistoryQuery(int limit,Cursor cursor) {
    public record Cursor(Instant createdAt,String id) {}
    private static final Set<String> PARAMETERS=Set.of("view","limit","cursor");
    private static final Base64.Encoder ENCODER=Base64.getUrlEncoder().withoutPadding();

    static TaskHistoryQuery parse(String workspace,MultiValueMap<String,String> parameters) {
        if (parameters.isEmpty()) return null; // The original no-parameter API remains unchanged.
        if (!PARAMETERS.containsAll(parameters.keySet()) || parameters.values().stream().anyMatch(v->v.size()!=1))
            throw ApiError.bad("Task history accepts single view, limit and cursor parameters only");
        if (!"summary".equals(parameters.getFirst("view"))) throw ApiError.bad("Pagination requires view=summary");
        String rawLimit=parameters.getFirst("limit");
        int limit=25;
        if (rawLimit!=null) {
            if (!rawLimit.matches("[1-9][0-9]{0,2}")) throw ApiError.bad("Task history limit must be an integer from 1 to 100");
            limit=Integer.parseInt(rawLimit);
            if (limit>100) throw ApiError.bad("Task history limit must be an integer from 1 to 100");
        }
        return new TaskHistoryQuery(limit,parameters.containsKey("cursor")?decode(workspace,parameters.getFirst("cursor")):null);
    }

    static String encode(String workspace,String createdAt,String id) {
        return "v1."+ENCODER.encodeToString((workspace+"|"+createdAt+"|"+id).getBytes(StandardCharsets.US_ASCII));
    }

    private static Cursor decode(String workspace,String token) {
        try {
            if (token==null || token.length()>192 || !token.matches("v1\\.[A-Za-z0-9_-]+")) throw new IllegalArgumentException();
            String encoded=token.substring(3);
            byte[] bytes=Base64.getUrlDecoder().decode(encoded);
            if (!encoded.equals(ENCODER.encodeToString(bytes))) throw new IllegalArgumentException();
            String[] fields=new String(bytes,StandardCharsets.US_ASCII).split("\\|",-1);
            if (fields.length!=3 || !workspace.equals(fields[0]) || !canonicalUuid(fields[0]) || !canonicalUuid(fields[2]))
                throw new IllegalArgumentException();
            if (!fields[1].matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z"))
                throw new IllegalArgumentException();
            Instant time=Instant.parse(fields[1]);
            if (!time.toString().equals(fields[1]) || time.atOffset(ZoneOffset.UTC).getYear()<1000 || time.getNano()%1000!=0)
                throw new IllegalArgumentException();
            return new Cursor(time,fields[2]);
        } catch (RuntimeException malformed) {
            throw ApiError.bad("Invalid task-history cursor for this workspace");
        }
    }

    private static boolean canonicalUuid(String value) {
        return value.length()==36 && UUID.fromString(value).toString().equals(value);
    }
}
