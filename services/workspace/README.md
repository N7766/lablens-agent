# LabLens JVM workspace service

Java 21 target, Spring Boot 3.5.16, JDBC/Flyway, MySQL and Redis. This service owns durable workspaces, document versions, idempotent task submission and bounded background execution. It calls the existing Python RAG worker at `POST /api/rag/answer`; it does not duplicate the RAG implementation.

## Run

Run a real MySQL instance and Redis first. Supply credentials through environment variables; there is no default database password.

```sh
export LABLENS_DB_URL='jdbc:mysql://127.0.0.1:3307/lablens_workspace?connectionTimeZone=UTC&forceConnectionTimeZoneToSession=true'
export LABLENS_DB_USER=lablens
# Set LABLENS_DB_PASSWORD using your local secret mechanism.
export LABLENS_REDIS_PORT=6380
export LABLENS_WORKER_BASE_URL=http://127.0.0.1:8765
mvn test package
java -jar target/lablens-workspace-0.12.0.jar
```

The default bind address is `127.0.0.1`, port `8081`. This is a single-user local prototype without authentication. Binding to another interface is an explicit deployment choice and does not add tenant security. Workspace IDs provide data scoping, not authentication.

## API contract

All paths below start with `/api`.

| Method and path | Request / response |
| --- | --- |
| GET `/workspaces` | `{workspaces: [...]}` |
| POST `/workspaces` | `{name}` → `201 {id,name,revision,created_at}` |
| GET `/workspaces/{id}/documents` | `{workspace_id,revision,documents:[{id,title,text,source,version,updated_at}]}` |
| GET `/workspaces/{id}/documents/{documentId}` | Document or 404 |
| PUT `/workspaces/{id}/documents/{documentId}` | `{title,text,source}` → document with new workspace revision |
| DELETE `/workspaces/{id}/documents/{documentId}` | 204; stores a versioned tombstone |
| POST `/workspaces/{id}/tasks` | Required `Idempotency-Key`; `{question,strategy,mode,top_k}` → task, 202 new / 200 reused |
| GET `/workspaces/{id}/tasks` | `{tasks:[...]}`; most recent 100 |
| GET `/workspaces/{id}/tasks/{taskId}` | Task including persisted result, error and timing |
| GET `/system/status` | MySQL counts, Redis state, actual process counters/timers, JVM and worker configuration |

Strategies: `bm25`, `dense`, `hybrid`. Modes: `extractive`, `live`. Defaults: hybrid / extractive / top_k 5. Validation rejects unknown JSON fields, invalid enum values, top_k outside 1–10, questions over 2000 characters, documents over 20000 characters, more than 100 current documents or 200000 current document text characters per workspace. Document IDs and idempotency keys contain 1–128 ASCII letters, digits, dots, underscores, colons or hyphens and start with a letter or digit. URL-encode IDs in path segments. Error objects contain `error` and `message`.

Task fields include `id`, `workspace_id`, `request`, `request_hash`, `snapshot_revision`, `snapshot_document_count`, `status`, `result`, `cache_hit`, `execution_usage`, `usage_unknown`, `error_code`, `error_message`, `error_result`, `worker_ms`, `created_at`, `started_at`, `finished_at`. POST additionally returns `reused`. Omitted or blank document source labels normalize to `document:<id>`, including legacy blank labels when capturing a snapshot.

## Consistency and failure handling

- MySQL is the source of truth. Workspace row locks serialize document changes and snapshot capture. Each task stores the complete immutable request/document snapshot and revision; later edits and deletion cannot change a queued analysis.
- A unique `(workspace_id,idempotency_key)` database constraint backs deduplication. Repeating a key with the same normalized request returns the original task, even after document edits. A different request with that key returns 409. Different keys represent different tasks, including for identical content.
- Claiming uses MySQL `FOR UPDATE SKIP LOCKED`. The default executor permits two active tasks per JVM and has bounded in-memory capacity. The durable queue is MySQL; each workspace defaults to 200 pending tasks. These are local/per-workspace limits, not a distributed global concurrency quota.
- Committed HTTP submissions and worker completion signal a dedicated dispatch thread. Signals coalesce into at most one waiting wakeup, so HTTP threads do not drain the database queue and bursts do not create an unbounded scheduling backlog. The existing 250 ms poll remains the restart/missed-signal fallback. Fast tasks can start the next queued task immediately after releasing their slot instead of waiting for the next poll.
- The periodic fallback signals that same dispatcher rather than creating a parallel claim loop. Claims use `READ_COMMITTED` to avoid unnecessary range gap locks. Expiry discovers candidate IDs through the `(status,lease_until)` index without locking, then rechecks expiry through primary-key writes; it does not range-update unexpired running tasks. Claim/completion/failure writes retry only rollback-safe database lock conflicts, at most twice with 10/20 ms pauses. These retries never repeat worker HTTP or provider calls.
- Completion requires the same owner, `RUNNING` state and an unexpired lease. A late response cannot overwrite an expired or completed task. Queued tasks survive restart. Expired running tasks become `FAILED`; live tasks and dense/hybrid extractive tasks record unknown external usage because embeddings can incur provider cost. Unknown request/token counts are null rather than fabricated zeros. There is no automatic model retry after an uncertain outcome.
- Redis caches only successful extractive results, keyed by namespace, workspace, revision and canonical request hash. Live tasks always bypass the result cache. Concurrent different-key cache misses are not coalesced. The configurable cache namespace must change if worker behavior or embedding configuration changes.
- `execution_usage` records the current task's provider usage. Cache hits have zero current usage and `cache_reused=true`; historical usage is explicitly labelled `origin_usage` on the task and result. `result.usage` also represents zero current usage on a cache hit. Redis lookup hits, validated cache hits and rejected cache entries have separate metrics. Invalid cached payloads are discarded before recomputation.
- A worker HTTP 200 alone is insufficient: the response must have a supported success status, matching mode, snapshot identity and retrieval strategy, and a well-formed usage object. Structured error details can preserve safe, allowlisted usage counters only when they match the task; arbitrary provider error text is never returned. `error_result` and `execution_usage` retain known partial usage from failed calls.
- Redis applies an atomic Lua fixed-window submission limit per workspace. Reused idempotency keys do not consume the limit. Redis outages fail open for cache/rate limiting, while MySQL deduplication remains authoritative; degradation is visible in status/metrics. Redis is not the durable queue.
- Database unavailability fails HTTP operations with 503. Worker errors/timeouts produce durable task failures and safe error messages; external provider response bodies are not echoed. This service does not infer that an unknown outcome had zero model cost.

## Configuration

| Variable | Default |
| --- | --- |
| `LABLENS_WORKSPACE_BIND` / `LABLENS_WORKSPACE_PORT` | `127.0.0.1` / `8081` |
| `LABLENS_DB_URL` / `LABLENS_DB_USER` / `LABLENS_DB_PASSWORD` | local port 3307 database `lablens_workspace` / `lablens` / empty |
| `LABLENS_REDIS_HOST` / `LABLENS_REDIS_PORT` / `LABLENS_REDIS_PASSWORD` | `127.0.0.1` / `6380` / empty |
| `LABLENS_WORKER_BASE_URL` | `http://127.0.0.1:8765` |
| `LABLENS_WORKER_TIMEOUT_SECONDS` | 90 |
| `LABLENS_WORKERS` | 2 (allowed 1–32) |
| `LABLENS_TASK_LEASE_SECONDS` | 180; must exceed worker timeout |
| `LABLENS_CACHE_TTL_SECONDS` | 300 |
| `LABLENS_CACHE_NAMESPACE` | `v012-rag-v1` |
| `LABLENS_RATE_LIMIT` | 120 submissions per 60-second window; nonpositive disables |
| `LABLENS_MAX_PENDING_TASKS` | 200 per workspace |
| `LABLENS_DISPATCH_ENABLED` | true; false enables deterministic storage tests |

Metrics are available at `/actuator/metrics` and `/actuator/prometheus`. JVM/memory/HTTP metrics are provided by Micrometer; `lablens.*` counters track actual submissions, duplicates, task outcomes, cache decisions, Redis degradation and worker concurrency. Process counters reset on restart; task counts and timings remain persisted in MySQL. No load test, SLA, production throughput or model-accuracy claim follows from simply exposing these metrics.

## Tests

```sh
# Pure JVM tests; real-service integration tests are explicitly skipped.
mvn test

# With MySQL + Redis and the environment above configured:
LABLENS_INTEGRATION_TESTS=true mvn test
```

Integration tests use genuine MySQL and Redis (no H2 substitution), create UUID-scoped fixtures and remove their database rows afterward. They cover concurrent idempotency, conflicts, immutable snapshots, document versioning/deletion, workspace scoping, capacity rollback, lease expiration, stale-owner completion, real cache revision separation/TTL and HTTP validation. They disable automatic dispatch and do not call an LLM. Cross-service RAG/load evaluations are separate evidence in the repository evaluation reports.
