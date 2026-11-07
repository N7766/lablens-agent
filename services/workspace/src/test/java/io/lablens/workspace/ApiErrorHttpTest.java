package io.lablens.workspace;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

class ApiErrorHttpTest {
    private final MockMvc http=MockMvcBuilders.standaloneSetup(new FailureEndpoint())
        .setControllerAdvice(new ApiErrors()).build();

    @Test void rateLimitedResponseTellsClientsToRetryWithAFixedWindowDelay() throws Exception {
        http.perform(get("/failure/rate_limited"))
            .andExpect(status().isTooManyRequests()).andExpect(header().string("Retry-After","60"))
            .andExpect(jsonPath("$.error").value("rate_limited"))
            .andExpect(jsonPath("$.retryable").value(true))
            .andExpect(jsonPath("$.retry_after_seconds").value(60));
    }
    @Test void workspaceSaturationAndGlobalOrRedisUnavailabilityHaveDifferentStatusCodes() throws Exception {
        for (String code:new String[]{"queue_full","global_queue_full","admission_unavailable"}) {
            http.perform(get("/failure/{code}",code))
                .andExpect(status().is("queue_full".equals(code)?429:503))
                .andExpect(header().string("Retry-After","1"))
                .andExpect(jsonPath("$.error").value(code))
                .andExpect(jsonPath("$.retryable").value(true))
                .andExpect(jsonPath("$.retry_after_seconds").value(1));
        }
    }
    @Test void idempotencyConflictPreservesItsExistingNonRetryableContract() throws Exception {
        http.perform(get("/failure/idempotency_conflict"))
            .andExpect(status().isConflict()).andExpect(header().doesNotExist("Retry-After"))
            .andExpect(content().json("{\"error\":\"idempotency_conflict\",\"message\":\"test failure\"}",true));
    }
    @Test void unrelatedDatabaseFailurePreservesItsExistingResponseBody() throws Exception {
        http.perform(get("/failure/database"))
            .andExpect(status().isServiceUnavailable()).andExpect(header().doesNotExist("Retry-After"))
            .andExpect(jsonPath("$.error").value("database_unavailable"))
            .andExpect(jsonPath("$.retryable").doesNotExist())
            .andExpect(jsonPath("$.retry_after_seconds").doesNotExist());
    }

    @RestController
    static class FailureEndpoint {
        @GetMapping("/failure/{code}")
        String fail(@PathVariable("code") String code) {
            if ("database".equals(code)) throw new DataAccessResourceFailureException("test outage");
            HttpStatus status=switch (code) {
                case "queue_full", "rate_limited" -> HttpStatus.TOO_MANY_REQUESTS;
                case "global_queue_full", "admission_unavailable" -> HttpStatus.SERVICE_UNAVAILABLE;
                default -> HttpStatus.CONFLICT;
            };
            throw new ApiError(status,code,"test failure");
        }
    }
}
