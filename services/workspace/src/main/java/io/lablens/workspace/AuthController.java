package io.lablens.workspace;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.Map;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class AuthController {
    private final AuthStore auth;
    private final AuthSettings settings;
    public record Login(String username,String password) {}
    public record NewUser(String username,String password,Boolean admin) {}
    public record Membership(String role) {}
    public record Adoption(String username) {}
    public AuthController(AuthStore auth,AuthSettings settings) { this.auth=auth; this.settings=settings; }
    @GetMapping("/api/auth/session") public Object session(HttpServletRequest request) {
        return auth.sessionBody((AuthStore.Identity)request.getAttribute(AuthStore.PRINCIPAL));
    }
    @PostMapping("/api/auth/login") public Object login(@RequestBody Login input,HttpServletRequest request,HttpServletResponse response) {
        enabled();
        AuthStore.Identity identity=auth.login(input.username(),input.password(),request.getRemoteAddr());
        // Re-authentication rotates this browser's current session, never accepts a supplied token.
        if (request.getAttribute(AuthStore.PRINCIPAL) instanceof AuthStore.Identity previous) auth.logout(previous);
        cookie(response,identity.token(),settings.sessionSeconds());
        response.setHeader("X-LabLens-User-Id",identity.id());
        return auth.sessionBody(identity);
    }
    @PostMapping("/api/auth/logout") public Object logout(HttpServletRequest request,HttpServletResponse response) {
        enabled(); auth.logout(AuthStore.requireIdentity(request)); cookie(response,"",0); return Map.of("authenticated",false);
    }
    private void cookie(HttpServletResponse response,String value,int seconds) {
        response.addHeader("Set-Cookie",ResponseCookie.from(AuthFilter.COOKIE,value).path("/").httpOnly(true)
            .secure(settings.secureCookie()).sameSite("Strict").maxAge(seconds).build().toString());
    }
    private void enabled() { if (!settings.enabled()) throw AuthStore.forbidden(); }
    @PostMapping("/api/auth/users") public ResponseEntity<?> user(@RequestBody NewUser input) {
        enabled(); return ResponseEntity.status(201).body(auth.createUser(input.username(),input.password(),Boolean.TRUE.equals(input.admin())));
    }
    @GetMapping("/api/auth/unowned-workspaces") public Object unowned() { enabled(); return Map.of("workspaces",auth.unowned()); }
    @PostMapping("/api/auth/workspaces/{id}/adopt") public Object adopt(@PathVariable String id,@RequestBody Adoption input) {
        enabled(); auth.adopt(id,input.username()); return Map.of("workspace_id",id,"owner",input.username());
    }
    @GetMapping("/api/workspaces/{id}/members") public Object members(@PathVariable String id) { enabled(); return Map.of("members",auth.members(id)); }
    @PutMapping("/api/workspaces/{id}/members/{username}") public Object member(@PathVariable String id,@PathVariable String username,@RequestBody Membership input) {
        enabled(); if (input.role()==null) throw ApiError.bad("Member role is required.");
        auth.setMember(id,username,input.role(),auth.current()); return Map.of("members",auth.members(id));
    }
    @DeleteMapping("/api/workspaces/{id}/members/{username}") public ResponseEntity<?> remove(@PathVariable String id,@PathVariable String username) {
        enabled(); auth.setMember(id,username,null,auth.current()); return ResponseEntity.noContent().build();
    }
}
