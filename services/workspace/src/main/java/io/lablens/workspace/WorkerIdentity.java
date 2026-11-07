package io.lablens.workspace;

import java.net.http.HttpRequest;

/** Browser sessions never cross the worker boundary. The service key remains server-side. */
final class WorkerIdentity {
    private WorkerIdentity() {}
    static HttpRequest.Builder apply(HttpRequest.Builder request,String key) {
        if (key!=null && !key.isEmpty()) {
            if (!AuthSettings.validServiceKey(key)) throw new IllegalStateException("Invalid worker service identity configuration");
            request.header("X-LabLens-Service-Key",key);
        }
        return request;
    }
}
