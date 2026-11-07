package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import java.net.URI;
import java.net.http.HttpRequest;
import org.junit.jupiter.api.Test;

class AuthSettingsTest {
    private AuthSettings settings(boolean enabled,String bind,String key) {
        return new AuthSettings(enabled,"http://127.0.0.1:8086",false,28800,"admin","do not expose this",20,bind,key);
    }
    @Test void defaultEnabledConfigurationFailsClosedWithoutServiceKey() {
        assertThrows(IllegalArgumentException.class,()->settings(true,"127.0.0.1",""));
        assertThrows(IllegalArgumentException.class,()->settings(false,"0.0.0.0",""));
        assertDoesNotThrow(()->settings(false,"127.0.0.1",""));
        assertDoesNotThrow(()->settings(true,"0.0.0.0","test-service-key-12345678901234567"));
    }
    @Test void ambiguousServiceKeysAndPasswordTruncationAreRejected() {
        for(String key:new String[]{"short","x".repeat(32)+",y","x".repeat(32)+" y","x".repeat(32)+"\n"}) assertFalse(AuthSettings.validServiceKey(key));
        assertThrows(ApiError.class,()->AuthStore.password("x".repeat(73)));
        assertThrows(ApiError.class,()->AuthStore.password("界".repeat(25)));
        assertDoesNotThrow(()->AuthStore.password("界".repeat(24)));
        assertFalse(settings(false,"127.0.0.1","").toString().contains("do not expose"));
    }
    @Test void workerIdentityHeaderIsPresentOnlyForConfiguredServiceIdentity() {
        String key="test-service-key-12345678901234567";
        var request=WorkerIdentity.apply(HttpRequest.newBuilder(URI.create("http://127.0.0.1/worker")),key).GET().build();
        assertEquals(key,request.headers().firstValue("X-LabLens-Service-Key").orElseThrow());
        assertThrows(IllegalStateException.class,()->WorkerIdentity.apply(HttpRequest.newBuilder(),"bad"));
        assertTrue(WorkerIdentity.apply(HttpRequest.newBuilder(URI.create("http://127.0.0.1/worker")),"").GET().build().headers().map().isEmpty());
    }
}
