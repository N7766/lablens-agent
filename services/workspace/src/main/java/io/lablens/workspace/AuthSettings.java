package io.lablens.workspace;

import java.net.URI;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/** Explicit local deployment boundary. Forwarded headers never determine the trusted origin. */
@Component
public record AuthSettings(boolean enabled,String origin,boolean secureCookie,int sessionSeconds,
                           String bootstrapUsername,String bootstrapPassword,int loginAttempts) {
    @Autowired
    public AuthSettings(@Value("${lablens.auth.enabled:true}") boolean enabled,
                        @Value("${lablens.auth.origin}") String origin,
                        @Value("${lablens.auth.secure-cookie:false}") boolean secureCookie,
                        @Value("${lablens.auth.session-seconds:28800}") int sessionSeconds,
                        @Value("${lablens.auth.bootstrap-username:admin}") String bootstrapUsername,
                        @Value("${lablens.auth.bootstrap-password:}") String bootstrapPassword,
                        @Value("${lablens.auth.login-attempts-per-minute:20}") int loginAttempts,
                        @Value("${server.address:127.0.0.1}") String bind,
                        @Value("${lablens.service-key:}") String serviceKey) {
        this(enabled,origin,secureCookie,sessionSeconds,bootstrapUsername,bootstrapPassword,loginAttempts);
        if (!enabled && !loopback(bind)) throw new IllegalArgumentException("Authentication-disabled development requires an explicit loopback server.address");
        URI uri;
        try { uri=URI.create(origin); } catch (RuntimeException e) { throw new IllegalArgumentException("Invalid authentication origin"); }
        if (!java.util.Set.of("http","https").contains(uri.getScheme()) || !loopback(uri.getHost())
                || uri.getRawUserInfo()!=null || uri.getRawQuery()!=null || uri.getRawFragment()!=null
                || (uri.getRawPath()!=null && !uri.getRawPath().isEmpty()))
            throw new IllegalArgumentException("Authentication origin must be an exact loopback HTTP(S) origin without a path");
        if (sessionSeconds<60 || sessionSeconds>86400 || loginAttempts<1 || loginAttempts>1000)
            throw new IllegalArgumentException("Authentication limits are out of range");
        if (enabled && !validServiceKey(serviceKey)) throw new IllegalArgumentException("Authentication requires a 32-512 character service key");
    }
    static boolean loopback(String value) { return java.util.Set.of("127.0.0.1","::1","[::1]","0:0:0:0:0:0:0:1").contains(value); }
    static boolean validServiceKey(String value) { return value!=null && value.matches("[!-+\\--~]{32,512}"); }
    @Override public String toString() { return "AuthSettings[enabled="+enabled+", secrets=redacted]"; }
}
