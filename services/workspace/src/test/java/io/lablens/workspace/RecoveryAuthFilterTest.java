package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.util.ReflectionTestUtils;

class RecoveryAuthFilterTest {
    static final String KEY="round18-fixture-service-key-1234567890";
    AuthFilter filter(String key) {
        var settings=new AuthSettings(false,"http://127.0.0.1:8081",false,3600,"admin","",20);
        var filter=new AuthFilter(mock(AuthStore.class),settings,new ObjectMapper());ReflectionTestUtils.setField(filter,"serviceKey",key);return filter;
    }
    MockHttpServletRequest request() {
        var request=new MockHttpServletRequest("POST","/api/internal/tasks/"+UUID.randomUUID()+"/checkpoint");
        request.setRemoteAddr("127.0.0.1");request.addHeader("X-LabLens-Execution-Owner",UUID.randomUUID().toString());return request;
    }
    @Test void disabledBrowserAuthenticationNeverBypassesInternalServiceIdentity() throws Exception {
        for(String configured:java.util.List.of("",KEY)) {
            var response=new MockHttpServletResponse();AtomicBoolean reached=new AtomicBoolean();
            filter(configured).doFilter(request(),response,(r,s)->reached.set(true));assertEquals(401,response.getStatus());assertFalse(reached.get());
        }
    }
    @Test void dedicatedKeyAndOwnerAllowOnlyTheExactInternalPost() throws Exception {
        var request=request();request.addHeader("X-LabLens-Service-Key",KEY);
        var response=new MockHttpServletResponse();AtomicBoolean reached=new AtomicBoolean();filter(KEY).doFilter(request,response,(r,s)->reached.set(true));
        assertTrue(reached.get());assertEquals(200,response.getStatus());
        request=request();request.addHeader("X-LabLens-Service-Key",KEY);request.setMethod("GET");
        response=new MockHttpServletResponse();reached.set(false);filter(KEY).doFilter(request,response,(r,s)->reached.set(true));assertEquals(401,response.getStatus());assertFalse(reached.get());
    }
}
