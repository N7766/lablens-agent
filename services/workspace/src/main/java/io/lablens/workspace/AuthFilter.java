package io.lablens.workspace;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.*;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.dao.DataAccessException;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.filter.OncePerRequestFilter;

/** One deny-by-default HTTP boundary also covers legacy controllers and future workspace routes. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+10)
public class AuthFilter extends OncePerRequestFilter {
    static final String COOKIE="LABLENS_SESSION";
    private final AuthStore auth;
    private final AuthSettings settings;
    private final ObjectMapper json;
    @Value("${lablens.service-key:}") private String serviceKey="";
    public AuthFilter(AuthStore auth,AuthSettings settings,ObjectMapper json) { this.auth=auth; this.settings=settings; this.json=json; }
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        response.setHeader("X-Frame-Options","DENY");
        response.setHeader("Content-Security-Policy","frame-ancestors 'none'");
        response.setHeader("Referrer-Policy","no-referrer");
        try {
            String path=request.getRequestURI();
            // Refuse aliases before servlet decoding or MVC path matching can disagree with ACL routing.
            String canonical=path;
            // Existing document IDs permit ':'; encodeURIComponent emits %3A. No other
            // encoded characters are required by the already enforced ASCII ID contract.
            if (path.matches("/api/workspaces/[0-9a-f-]{36}/documents/[^/]+")) canonical=path.replaceAll("(?i)%3a",":");
            if (canonical.contains("%") || canonical.contains(";") || canonical.contains("\\") || canonical.contains("//") || canonical.contains("/../") || canonical.contains("/./"))
                throw ApiError.bad("Use a canonical request path without encoded separators or path parameters.");
            if (!settings.enabled() && !AuthSettings.loopback(request.getRemoteAddr())) throw AuthStore.forbidden();
            boolean api=path.equals("/api") || path.startsWith("/api/");
            boolean actuator=path.startsWith("/actuator");
            if (!api && !actuator) { chain.doFilter(request,response); return; }
            response.setHeader("Cache-Control","no-store");
            response.setHeader("X-Content-Type-Options","nosniff");
            // Only this exact callback accepts machine credentials. Browser/session ACLs
            // remain unchanged for every workspace and administrative route.
            if (path.startsWith("/api/internal/")) {
                if (!path.matches("/api/internal/tasks/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/checkpoint")
                    || !request.getMethod().equals("POST") || !AuthSettings.validServiceKey(serviceKey)
                    || !AuthStore.equal(serviceKey,single(request,"X-LabLens-Service-Key"))
                    || request.getHeader("Authorization")!=null || request.getHeader("Cookie")!=null
                    || request.getCookies()!=null || request.getHeader("Origin")!=null)
                    throw new ApiError(org.springframework.http.HttpStatus.UNAUTHORIZED,"invalid_service_identity","Checkpoint writes require the dedicated service identity.");
                String owner=single(request,"X-LabLens-Execution-Owner");
                if (owner==null || !owner.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
                    throw new ApiError(org.springframework.http.HttpStatus.UNAUTHORIZED,"invalid_execution_owner","Checkpoint writes require the active execution owner.");
                chain.doFilter(request,response);return;
            }
            if (!settings.enabled()) { chain.doFilter(request,response); return; }
            if (path.equals("/actuator/health") && request.getMethod().equals("GET")) { chain.doFilter(request,response); return; }
            if (request.getHeader("Authorization")!=null || request.getHeader("X-LabLens-Service-Key")!=null)
                throw new ApiError(org.springframework.http.HttpStatus.UNAUTHORIZED,"unsupported_credentials","Use the workspace session cookie.");
            String token=cookie(request);
            AuthStore.Identity who=auth.session(token);
            if (who!=null) {
                request.setAttribute(AuthStore.PRINCIPAL,who);
                response.setHeader("X-LabLens-User-Id",who.id());
            }
            if (path.equals("/api/auth/session") && request.getMethod().equals("GET")) { chain.doFilter(request,response); return; }
            if (path.equals("/api/auth/login") && request.getMethod().equals("POST")) {
                origin(request);
                String contentType=single(request,"Content-Type");
                if (contentType==null || !MediaType.APPLICATION_JSON.isCompatibleWith(MediaType.parseMediaType(contentType)))
                    throw ApiError.bad("Login requires application/json.");
                chain.doFilter(request,response); return;
            }
            if (who==null) throw AuthStore.unauthorized();
            boolean write=!Set.of("GET","HEAD").contains(request.getMethod());
            if (write) {
                origin(request);
                if (!AuthStore.equal(who.csrf(),single(request,"X-CSRF-Token")))
                    throw new ApiError(org.springframework.http.HttpStatus.FORBIDDEN,"csrf_failed","Refresh your session before retrying this action.");
            }
            if (path.startsWith("/api/workspaces/")) {
                String[] segments=path.split("/",-1);
                String workspace=segments.length>3?segments[3]:"";
                if (!workspace.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) throw ApiError.missing("Workspace does not exist or is not accessible.");
                boolean members=segments.length>4 && segments[4].equals("members");
                auth.access(workspace,who,write,members);
            } else if (path.equals("/api/workspaces") || path.equals("/api/auth/logout")) {
                // Every authenticated user can list their own workspaces and create an owned one.
            } else if (path.equals("/api/system/status") || actuator || path.startsWith("/api/auth/")) {
                if (!who.admin()) throw AuthStore.forbidden();
            } else throw AuthStore.forbidden();
            chain.doFilter(request,response);
        } catch (ApiError error) {
            response.setStatus(error.status.value()); response.setContentType("application/json");
            if (error.code.equals("login_rate_limited")) response.setHeader("Retry-After","60");
            json.writeValue(response.getWriter(),Map.of("error",error.code,"message",error.getMessage()));
        } catch (DataAccessException error) {
            response.setStatus(503); response.setContentType("application/json");
            json.writeValue(response.getWriter(),Map.of("error","authentication_unavailable","message","Authentication is temporarily unavailable."));
        } catch (org.springframework.http.InvalidMediaTypeException error) {
            response.setStatus(400); response.setContentType("application/json");
            json.writeValue(response.getWriter(),Map.of("error","invalid_request","message","Invalid content type."));
        }
    }
    private void origin(HttpServletRequest request) {
        if (!settings.origin().equals(single(request,"Origin")))
            throw new ApiError(org.springframework.http.HttpStatus.FORBIDDEN,"origin_rejected","Request origin does not match this workspace service.");
        String site=single(request,"Sec-Fetch-Site");
        if (site!=null && !Set.of("same-origin","none").contains(site)) throw AuthStore.forbidden();
    }
    static String single(HttpServletRequest request,String name) {
        List<String> values=Collections.list(request.getHeaders(name));
        if (values.size()>1 || (!values.isEmpty() && values.getFirst().contains(","))) throw ApiError.bad("Ambiguous request headers are not accepted.");
        return values.isEmpty()?null:values.getFirst();
    }
    static String cookie(HttpServletRequest request) {
        String found=null;
        for (String header:Collections.list(request.getHeaders("Cookie"))) {
            for (String pair:header.split(";")) {
                String[] parts=pair.strip().split("=",2);
                if (parts[0].equals(COOKIE)) {
                    if (found!=null || parts.length!=2 || !parts[1].matches("[A-Za-z0-9_-]{43}")) throw ApiError.bad("Ambiguous or invalid session cookie.");
                    found=parts[1];
                }
            }
        }
        // Mock servlet requests can represent cookies without creating the raw Cookie header.
        if (Collections.list(request.getHeaders("Cookie")).isEmpty() && request.getCookies()!=null) {
            for (Cookie cookie:request.getCookies()) if (COOKIE.equals(cookie.getName())) {
                if (found!=null || !cookie.getValue().matches("[A-Za-z0-9_-]{43}")) throw ApiError.bad("Ambiguous or invalid session cookie.");
                found=cookie.getValue();
            }
        }
        return found;
    }
}
