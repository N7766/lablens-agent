package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.Cookie;
import java.net.URI;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/** Real MySQL sessions, memberships, migration quarantine and shared admission; no fake auth database. */
@SpringBootTest(properties={"lablens.auth.enabled=true","lablens.auth.origin=http://127.0.0.1:8086",
    "lablens.auth.bootstrap-username=round16admin","lablens.auth.bootstrap-password=round16-fixture-admin-password",
    "lablens.service-key=round16-fixture-service-key-1234567890","lablens.auth.login-attempts-per-minute=1000",
    "lablens.dispatch-enabled=false","lablens.import-dispatch-enabled=false","lablens.mq.enabled=false",
    "lablens.max-pending-tasks=2","lablens.rate-limit=10000"})
@AutoConfigureMockMvc
@AutoConfigureObservability
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class AuthIntegrationTest {
    static final String ORIGIN="http://127.0.0.1:8086";
    @Autowired AuthStore auth;
    @Autowired WorkspaceStore store;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    String suffix=UUID.randomUUID().toString().substring(0,8),aliceName="alice-"+suffix,bobName="bob-"+suffix;
    AuthStore.Identity admin,alice,bob;
    List<String> workspaces=new ArrayList<>();
    @BeforeAll void users() {
        // A previous failed test run can retain the bootstrap account; bootstrap never resets its hash.
        if (db.queryForObject("SELECT COUNT(*) FROM auth_users WHERE username='round16admin'",Integer.class)==0)
            auth.createUser("round16admin","round16-fixture-admin-password",true);
        auth.createUser(aliceName,"round16-fixture-alice-password",false);
        auth.createUser(bobName,"round16-fixture-bob-password",false);
        admin=auth.login("round16admin","round16-fixture-admin-password","127.0.0.1");
        alice=auth.login(aliceName,"round16-fixture-alice-password","127.0.0.1");
        bob=auth.login(bobName,"round16-fixture-bob-password","127.0.0.1");
    }
    @AfterEach void cleanup() {
        for (String workspace:workspaces) {
            db.update("DELETE FROM tasks WHERE workspace_id=?",workspace);
            db.update("DELETE FROM conversations WHERE workspace_id=?",workspace);
            db.update("DELETE FROM document_versions WHERE workspace_id=?",workspace);
            db.update("DELETE FROM documents WHERE workspace_id=?",workspace);
            db.update("DELETE FROM workspaces WHERE id=?",workspace);
        }
        workspaces.clear();
    }
    @AfterAll void usersCleanup() {
        auth.logout(admin); auth.logout(alice); auth.logout(bob);
        db.update("DELETE FROM auth_users WHERE username IN (?,?)",aliceName,bobName);
    }
    MockHttpServletRequestBuilder as(MockHttpServletRequestBuilder request,AuthStore.Identity identity) {
        return request.cookie(new Cookie(AuthFilter.COOKIE,identity.token())).header("Origin",ORIGIN).header("X-CSRF-Token",identity.csrf());
    }
    String workspace() throws Exception {
        var response=mvc.perform(as(post("/api/workspaces"),alice).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"Auth fixture\"}"))
            .andExpect(status().isCreated()).andExpect(jsonPath("role").value("OWNER")).andReturn();
        String id=json.readTree(response.getResponse().getContentAsString()).get("id").asText();workspaces.add(id);return id;
    }
    void member(String workspace,String role) throws Exception {
        mvc.perform(as(put("/api/workspaces/"+workspace+"/members/"+bobName),alice).contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(Map.of("role",role))))
            .andExpect(status().isOk());
    }
    @Test void sessionContainsNoBearerAndDatabaseContainsOnlyItsHash() throws Exception {
        mvc.perform(get("/api/auth/session")).andExpect(status().isOk()).andExpect(jsonPath("authenticated").value(false));
        String body=mvc.perform(as(get("/api/auth/session"),alice)).andExpect(status().isOk())
            .andExpect(jsonPath("user.username").value(aliceName)).andExpect(jsonPath("csrf_token").value(alice.csrf())).andReturn().getResponse().getContentAsString();
        assertFalse(body.contains(alice.token()));
        assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM auth_sessions WHERE token_hash=?",Integer.class,alice.token()));
        assertEquals(1,db.queryForObject("SELECT COUNT(*) FROM auth_sessions WHERE token_hash=?",Integer.class,Requests.sha256(alice.token())));
        assertTrue(db.queryForObject("SELECT password_hash FROM auth_users WHERE id=?",String.class,alice.id()).startsWith("$2a$12$"));
    }
    @Test void loginRequiresExactOriginAndJsonAndSetsCookieFlags() throws Exception {
        String body=json.writeValueAsString(Map.of("username",aliceName,"password","round16-fixture-alice-password"));
        mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isForbidden());
        mvc.perform(post("/api/auth/login").header("Origin","https://evil.invalid").contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isForbidden());
        mvc.perform(post("/api/auth/login").header("Origin",ORIGIN).contentType(MediaType.TEXT_PLAIN).content(body)).andExpect(status().isBadRequest());
        var response=mvc.perform(post("/api/auth/login").header("Origin",ORIGIN).contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isOk()).andReturn().getResponse();
        String cookie=response.getHeader("Set-Cookie");
        assertTrue(cookie.contains("HttpOnly"));assertTrue(cookie.contains("SameSite=Strict"));assertTrue(cookie.contains("Path=/"));
        String token=cookie.split(";",2)[0].split("=",2)[1];auth.logout(auth.session(token));
    }
    @Test void csrfAndOriginAreRequiredForAllAuthenticatedWrites() throws Exception {
        Cookie cookie=new Cookie(AuthFilter.COOKIE,alice.token());
        for (MockHttpServletRequestBuilder request:List.of(post("/api/workspaces"),post("/api/auth/logout"))) {
            mvc.perform(request.cookie(cookie).header("Origin",ORIGIN).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"blocked\"}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("error").value("csrf_failed"));
        }
        mvc.perform(post("/api/workspaces").cookie(cookie).header("X-CSRF-Token",alice.csrf()).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"blocked\"}"))
            .andExpect(status().isForbidden()).andExpect(jsonPath("error").value("origin_rejected"));
    }
    @Test void legacyReadsAndEveryMutationAreGuardedBeforeBodyParsing() throws Exception {
        String workspace=workspace(),base="/api/workspaces/"+workspace;
        for (String suffix:List.of("/documents","/tasks","/tasks?view=summary&limit=2","/conversations","/imports/not-real","/index")) {
            mvc.perform(get(base+suffix)).andExpect(status().isUnauthorized());
            mvc.perform(as(get(base+suffix),bob)).andExpect(status().isNotFound());
        }
        member(workspace,"VIEWER");
        for (String suffix:List.of("/documents","/tasks","/conversations")) mvc.perform(as(get(base+suffix),bob)).andExpect(status().isOk());
        for (String suffix:List.of("/tasks","/tasks/no/cancel","/conversations","/conversations/no/turns","/imports","/imports/no/cancel","/index"))
            mvc.perform(as(post(base+suffix),bob).contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("error").value("access_denied"));
        mvc.perform(as(put(base+"/documents/doc"),bob).contentType(MediaType.APPLICATION_JSON).content("{}")) .andExpect(status().isForbidden());
        mvc.perform(as(delete(base+"/documents/doc"),bob)).andExpect(status().isForbidden());
    }
    @Test void listIsScopedAndAdminCannotImplicitlyReadOwnedWorkspace() throws Exception {
        String workspace=workspace();
        String body=mvc.perform(as(get("/api/workspaces"),bob)).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertFalse(body.contains(workspace));
        mvc.perform(as(get("/api/workspaces/"+workspace+"/documents"),admin)).andExpect(status().isNotFound());
        member(workspace,"EDITOR");
        mvc.perform(as(get("/api/workspaces"),bob)).andExpect(jsonPath("workspaces[0].id").value(workspace)).andExpect(jsonPath("workspaces[0].role").value("EDITOR"));
    }
    @Test void onlyOwnerManagesMembersAndOwnerCannotBeDemotedOrRemoved() throws Exception {
        String workspace=workspace(),base="/api/workspaces/"+workspace+"/members";member(workspace,"EDITOR");
        mvc.perform(as(get(base),bob)).andExpect(status().isForbidden());
        mvc.perform(as(put(base+"/"+aliceName),alice).contentType(MediaType.APPLICATION_JSON).content("{\"role\":\"VIEWER\"}"))
            .andExpect(status().isConflict()).andExpect(jsonPath("error").value("owner_immutable"));
        mvc.perform(as(delete(base+"/"+aliceName),alice)).andExpect(status().isConflict());
        mvc.perform(as(delete(base+"/"+bobName),alice)).andExpect(status().isNoContent());
        mvc.perform(as(get("/api/workspaces/"+workspace+"/documents"),bob)).andExpect(status().isNotFound());
    }
    @Test void legacyWorkspaceIsQuarantinedUntilExplicitAdminAdoptionAndCannotBeStolen() throws Exception {
        String workspace=(String)store.createWorkspace(new Requests.Workspace("legacy unowned")).get("id");workspaces.add(workspace);
        mvc.perform(as(get("/api/workspaces/"+workspace+"/documents"),admin)).andExpect(status().isNotFound());
        String unowned=mvc.perform(as(get("/api/auth/unowned-workspaces"),admin)).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();assertTrue(unowned.contains(workspace));
        String endpoint="/api/auth/workspaces/"+workspace+"/adopt",body=json.writeValueAsString(Map.of("username",aliceName));
        mvc.perform(as(post(endpoint),alice).contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isForbidden());
        mvc.perform(as(post(endpoint),admin).contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isOk());
        mvc.perform(as(get("/api/workspaces/"+workspace+"/documents"),alice)).andExpect(status().isOk());
        mvc.perform(as(post(endpoint),admin).contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(Map.of("username",bobName))))
            .andExpect(status().isConflict()).andExpect(jsonPath("error").value("workspace_owned"));
    }
    @Test void expiryLogoutAndMembershipRevocationTakeEffectOnNextRequest() throws Exception {
        AuthStore.Identity temporary=auth.login(aliceName,"round16-fixture-alice-password","127.0.0.1");
        db.update("UPDATE auth_sessions SET expires_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE token_hash=?",Requests.sha256(temporary.token()));
        mvc.perform(as(get("/api/workspaces"),temporary)).andExpect(status().isUnauthorized());auth.logout(temporary);
        temporary=auth.login(aliceName,"round16-fixture-alice-password","127.0.0.1");
        mvc.perform(as(post("/api/auth/logout"),temporary)).andExpect(status().isOk());
        mvc.perform(as(get("/api/workspaces"),temporary)).andExpect(status().isUnauthorized());
        String workspace=workspace();member(workspace,"EDITOR");
        member(workspace,"VIEWER");
        mvc.perform(as(post("/api/workspaces/"+workspace+"/tasks"),bob).contentType(MediaType.APPLICATION_JSON).content("{}")) .andExpect(status().isForbidden());
    }
    @Test void operationalStatusAndMetricsRequireAdminButHealthRemainsPublic() throws Exception {
        mvc.perform(get("/actuator/health")).andExpect(status().isOk());
        for(String endpoint:List.of("/api/system/status","/actuator/metrics","/actuator/prometheus")) {
            mvc.perform(get(endpoint)).andExpect(status().isUnauthorized());
            mvc.perform(as(get(endpoint),alice)).andExpect(status().isForbidden());
            mvc.perform(as(get(endpoint),admin)).andExpect(status().isOk());
        }
    }
    @Test void userProvisioningRequiresAdminAndNeverReturnsPasswordHash() throws Exception {
        String name="provision-"+suffix,body=json.writeValueAsString(Map.of("username",name,"password","round16-provision-password"));
        mvc.perform(as(post("/api/auth/users"),alice).contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isForbidden());
        String result=mvc.perform(as(post("/api/auth/users"),admin).contentType(MediaType.APPLICATION_JSON).content(body)).andExpect(status().isCreated())
            .andExpect(jsonPath("admin").value(false)).andReturn().getResponse().getContentAsString();
        assertFalse(result.contains("password"));db.update("DELETE FROM auth_users WHERE username=?",name);
    }
    @Test void ambiguousCredentialsAndEncodedRouteAliasesCannotBypassAuthorization() throws Exception {
        mvc.perform(get("/api/workspaces").header("Cookie","LABLENS_SESSION="+alice.token()+"; LABLENS_SESSION="+bob.token())).andExpect(status().isBadRequest());
        mvc.perform(as(post("/api/auth/logout"),alice).header("X-CSRF-Token",alice.csrf())).andExpect(status().isBadRequest());
        mvc.perform(as(get("/api/workspaces"),alice).header("Authorization","Bearer ignored")).andExpect(status().isUnauthorized());
        for(String path:List.of("/api%2fworkspaces","/%61pi/workspaces","/api/workspaces;anything","/api//workspaces"))
            mvc.perform(get(URI.create(path))).andExpect(status().isBadRequest());
        mvc.perform(as(get("/api/unknown-future-endpoint"),alice)).andExpect(status().isForbidden());
    }
    @Test void encodedColonDocumentIdRetainsExistingContract() throws Exception {
        String workspace=workspace();store.putDocument(workspace,"source:one",new Requests.Document("One","Evidence","test://one"));
        mvc.perform(as(get(URI.create("/api/workspaces/"+workspace+"/documents/source%3Aone")),alice))
            .andExpect(status().isOk()).andExpect(jsonPath("id").value("source:one"));
    }
    @Test void sharedMembersUseTheSameWorkspaceQueueAndCannotBypassModelApproval() throws Exception {
        String workspace=workspace();member(workspace,"EDITOR");store.putDocument(workspace,"evidence",new Requests.Document("Evidence","This is evidence.","test://evidence"));
        String endpoint="/api/workspaces/"+workspace+"/tasks";
        mvc.perform(as(post(endpoint),bob).header("Idempotency-Key","approval").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"q\",\"mode\":\"agent\",\"strategy\":\"bm25\"}"))
            .andExpect(status().isConflict()).andExpect(jsonPath("error").value("approval_required"));
        for (AuthStore.Identity who:List.of(alice,bob)) mvc.perform(as(post(endpoint),who).header("Idempotency-Key",who.username()).contentType(MediaType.APPLICATION_JSON)
            .content("{\"question\":\"q\",\"mode\":\"extractive\",\"strategy\":\"bm25\"}")) .andExpect(status().isAccepted());
        mvc.perform(as(post(endpoint),bob).header("Idempotency-Key","another-member-attempt").contentType(MediaType.APPLICATION_JSON)
            .content("{\"question\":\"q\",\"mode\":\"extractive\",\"strategy\":\"bm25\"}"))
            .andExpect(status().isTooManyRequests()).andExpect(jsonPath("error").value("queue_full"));
        assertEquals(2,db.queryForObject("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",Integer.class,workspace));
    }
    @Test void loginAdmissionIsDurableAndPrecedesPasswordVerification() {
        String remote="192.0.2.16",key=Requests.sha256(remote);
        db.update("INSERT INTO auth_login_buckets(bucket_key,window_minute,attempts) VALUES (?,FLOOR(UNIX_TIMESTAMP(UTC_TIMESTAMP())/60),1000) ON DUPLICATE KEY UPDATE window_minute=FLOOR(UNIX_TIMESTAMP(UTC_TIMESTAMP())/60),attempts=1000",key);
        ApiError error=assertThrows(ApiError.class,()->auth.login(aliceName,"round16-fixture-alice-password",remote));
        assertEquals("login_rate_limited",error.code);db.update("DELETE FROM auth_login_buckets WHERE bucket_key=?",key);
    }
}
