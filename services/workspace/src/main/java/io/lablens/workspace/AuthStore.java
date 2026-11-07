package io.lablens.workspace;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

@Service
public class AuthStore implements ApplicationRunner {
    static final String PRINCIPAL=AuthStore.class.getName()+".principal";
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final AuthSettings settings;
    private final BCryptPasswordEncoder passwords=new BCryptPasswordEncoder(12);
    private final SecureRandom random=new SecureRandom();
    private final String dummyHash=passwords.encode("unused timing equalizer password");
    public record Identity(String id,String username,boolean admin,String token) {
        Map<String,Object> user() { return Map.of("id",id,"username",username,"admin",admin); }
        String csrf() { return Requests.sha256("lablens-csrf-v1:"+token); }
    }
    public AuthStore(JdbcTemplate db,TransactionTemplate tx,AuthSettings settings) { this.db=db; this.tx=tx; this.settings=settings; }
    @Override public void run(ApplicationArguments arguments) {
        if (!settings.enabled()) return;
        if (db.queryForObject("SELECT COUNT(*) FROM auth_users WHERE is_admin=TRUE",Integer.class)>0) return;
        if (settings.bootstrapPassword().isEmpty()) throw new IllegalStateException("Initial administrator requires LABLENS_AUTH_BOOTSTRAP_PASSWORD");
        createUser(settings.bootstrapUsername(),settings.bootstrapPassword(),true);
    }
    Identity current() {
        if (!settings.enabled()) return null;
        var request=((ServletRequestAttributes)RequestContextHolder.currentRequestAttributes()).getRequest();
        return requireIdentity(request);
    }
    static Identity requireIdentity(HttpServletRequest request) {
        if (!(request.getAttribute(PRINCIPAL) instanceof Identity who)) throw unauthorized();
        return who;
    }
    static ApiError unauthorized() { return new ApiError(HttpStatus.UNAUTHORIZED,"authentication_required","Sign in to continue."); }
    static ApiError forbidden() { return new ApiError(HttpStatus.FORBIDDEN,"access_denied","This operation is not permitted."); }
    static String username(String value) {
        if (value==null || !value.matches("[a-z][a-z0-9_.-]{2,63}")) throw ApiError.bad("Username must be 3-64 lowercase ASCII letters, digits, dot, dash or underscore, starting with a letter.");
        return value;
    }
    static void password(String value) {
        if (value==null || value.length()<12 || value.getBytes(StandardCharsets.UTF_8).length>72 || value.indexOf('\0')>=0)
            throw ApiError.bad("Password must contain at least 12 characters and at most 72 UTF-8 bytes.");
    }
    Map<String,Object> createUser(String name,String password,boolean admin) {
        username(name); password(password);
        String id=UUID.randomUUID().toString(),hash=passwords.encode(password);
        try { db.update("INSERT INTO auth_users(id,username,password_hash,is_admin) VALUES (?,?,?,?)",id,name,hash,admin); }
        catch (DuplicateKeyException e) { throw new ApiError(HttpStatus.CONFLICT,"username_exists","Username already exists."); }
        return Map.of("id",id,"username",name,"admin",admin);
    }
    Identity login(String name,String password,String remote) {
        rateLogin(remote);
        // Invalid usernames receive the same response and one bounded password check.
        boolean valid=name!=null && name.matches("[a-z][a-z0-9_.-]{2,63}");
        List<Map<String,Object>> users=valid?db.queryForList("SELECT id,username,password_hash,is_admin FROM auth_users WHERE username=?",name):List.of();
        String hash=users.isEmpty()?dummyHash:(String)users.getFirst().get("password_hash");
        boolean bounded=password!=null && password.getBytes(StandardCharsets.UTF_8).length<=72;
        boolean matches=passwords.matches(bounded?password:"invalid overlong password",hash);
        if (!bounded || !matches || users.isEmpty()) throw new ApiError(HttpStatus.UNAUTHORIZED,"invalid_credentials","Username or password is incorrect.");
        var user=users.getFirst();
        String token=token(),id=(String)user.get("id");
        db.update("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES (?,?,TIMESTAMPADD(SECOND,?,UTC_TIMESTAMP(6)))",Requests.sha256(token),id,settings.sessionSeconds());
        return new Identity(id,(String)user.get("username"),Boolean.TRUE.equals(user.get("is_admin")),token);
    }
    private void rateLogin(String remote) {
        String key=Requests.sha256(remote);
        boolean allowed=Boolean.TRUE.equals(tx.execute(status->{
            db.update("INSERT IGNORE INTO auth_login_buckets(bucket_key,window_minute,attempts) VALUES (?,FLOOR(UNIX_TIMESTAMP(UTC_TIMESTAMP())/60),0)",key);
            db.queryForObject("SELECT attempts FROM auth_login_buckets WHERE bucket_key=? FOR UPDATE",Integer.class,key);
            db.update("UPDATE auth_login_buckets SET attempts=IF(window_minute=FLOOR(UNIX_TIMESTAMP(UTC_TIMESTAMP())/60),attempts+1,1),window_minute=FLOOR(UNIX_TIMESTAMP(UTC_TIMESTAMP())/60) WHERE bucket_key=?",key);
            return db.queryForObject("SELECT attempts FROM auth_login_buckets WHERE bucket_key=?",Integer.class,key)<=settings.loginAttempts();
        }));
        if (!allowed) throw new ApiError(HttpStatus.TOO_MANY_REQUESTS,"login_rate_limited","Too many login attempts; wait for the next minute.");
    }
    private String token() { byte[] bytes=new byte[32]; random.nextBytes(bytes); return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }
    Identity session(String token) {
        if (token==null || !token.matches("[A-Za-z0-9_-]{43}")) return null;
        var rows=db.query("SELECT u.id,u.username,u.is_admin FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>UTC_TIMESTAMP(6)",
            (rs,n)->new Identity(rs.getString("id"),rs.getString("username"),rs.getBoolean("is_admin"),token),Requests.sha256(token));
        return rows.isEmpty()?null:rows.getFirst();
    }
    void logout(Identity identity) { db.update("DELETE FROM auth_sessions WHERE token_hash=?",Requests.sha256(identity.token())); }
    static boolean equal(String a,String b) { return a!=null && b!=null && MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8),b.getBytes(StandardCharsets.UTF_8)); }
    Map<String,Object> sessionBody(Identity identity) {
        Map<String,Object> result=new LinkedHashMap<>(); result.put("enabled",settings.enabled());
        result.put("authenticated",!settings.enabled() || identity!=null);
        result.put("user",identity==null?null:identity.user()); result.put("csrf_token",identity==null?null:identity.csrf()); return result;
    }
    String role(String workspace,String user) {
        var rows=db.queryForList("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",String.class,workspace,user);
        return rows.isEmpty()?null:rows.getFirst();
    }
    void access(String workspace,Identity identity,boolean write,boolean owner) {
        String role=role(workspace,identity.id());
        if (role==null) throw ApiError.missing("Workspace does not exist or is not accessible.");
        if ((owner && !role.equals("OWNER")) || (write && role.equals("VIEWER"))) throw forbidden();
    }
    List<Map<String,Object>> workspaces(Identity identity) {
        return db.query("SELECT w.id,w.name,w.revision,w.created_at,m.role FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created_at,w.id LIMIT 1000",
            (rs,n)->Map.of("id",rs.getString("id"),"name",rs.getString("name"),"revision",rs.getLong("revision"),"created_at",rs.getTimestamp("created_at").toInstant().toString(),"role",rs.getString("role")),identity.id());
    }
    Map<String,Object> createWorkspace(Requests.Workspace input,Identity identity,WorkspaceStore store) {
        return tx.execute(status->{
            Map<String,Object> workspace=new LinkedHashMap<>(store.createWorkspace(input));
            db.update("INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (?,?,'OWNER')",workspace.get("id"),identity.id());
            workspace.put("role","OWNER"); return workspace;
        });
    }
    List<Map<String,Object>> members(String workspace) {
        return db.query("SELECT u.id,u.username,m.role FROM workspace_members m JOIN auth_users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.role,u.username",
            (rs,n)->Map.of("id",rs.getString("id"),"username",rs.getString("username"),"role",rs.getString("role")),workspace);
    }
    private void lockWorkspace(String workspace) {
        if (db.queryForList("SELECT id FROM workspaces WHERE id=? FOR UPDATE",String.class,workspace).isEmpty()) throw ApiError.missing("Workspace does not exist.");
    }
    private String userId(String username) {
        username(username);
        var ids=db.queryForList("SELECT id FROM auth_users WHERE username=?",String.class,username);
        if (ids.isEmpty()) throw ApiError.missing("User does not exist."); return ids.getFirst();
    }
    void setMember(String workspace,String username,String role,Identity actor) {
        if (role!=null && !Set.of("EDITOR","VIEWER").contains(role)) throw ApiError.bad("Member role must be EDITOR or VIEWER.");
        tx.executeWithoutResult(status->{
            lockWorkspace(workspace); access(workspace,actor,false,true);
            String user=userId(username);
            if ("OWNER".equals(role(workspace,user))) throw new ApiError(HttpStatus.CONFLICT,"owner_immutable","The workspace owner cannot be changed or removed.");
            if (role==null) db.update("DELETE FROM workspace_members WHERE workspace_id=? AND user_id=?",workspace,user);
            else db.update("INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (?,?,?) ON DUPLICATE KEY UPDATE role=?",workspace,user,role,role);
        });
    }
    List<Map<String,Object>> unowned() {
        return db.queryForList("SELECT w.id,w.name,w.revision,w.created_at FROM workspaces w WHERE NOT EXISTS (SELECT 1 FROM workspace_members m WHERE m.workspace_id=w.id) ORDER BY w.created_at,w.id LIMIT 1000");
    }
    void adopt(String workspace,String username) {
        tx.executeWithoutResult(status->{
            lockWorkspace(workspace);
            if (db.queryForObject("SELECT COUNT(*) FROM workspace_members WHERE workspace_id=?",Integer.class,workspace)!=0)
                throw new ApiError(HttpStatus.CONFLICT,"workspace_owned","Only unowned workspaces can be adopted.");
            db.update("INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (?,?,'OWNER')",workspace,userId(username));
        });
    }
}
