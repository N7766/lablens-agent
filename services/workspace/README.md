# LabLens JVM workspace service

Candidate v0.13 integration; no v0.13 release is declared here. Historical v0.12 evaluations remain unchanged.

Java 21 target, Spring Boot 3.5.16, JDBC/Flyway, MySQL, Redis and optional RabbitMQ. This service owns durable workspaces, document versions, idempotent task submission and bounded background execution. It calls the existing Python RAG worker at `POST /api/rag/answer`; it does not duplicate the RAG implementation.

## Run

Run a real MySQL instance and Redis first. Supply credentials through environment variables; there is no default database password.

```sh
export LABLENS_DB_URL='jdbc:mysql://127.0.0.1:3307/lablens_workspace?connectionTimeZone=UTC&forceConnectionTimeZoneToSession=true'
export LABLENS_DB_USER=lablens
# Set LABLENS_DB_PASSWORD using your local secret mechanism.
export LABLENS_REDIS_PORT=6380
export LABLENS_WORKER_BASE_URL=http://127.0.0.1:8765
# Match the 120-second live RAG budget used in .env.example and Compose.
export LABLENS_WORKER_TIMEOUT_SECONDS=150
mvn test package
java -jar target/lablens-workspace-0.12.0.jar
```

Start the Python worker separately with `uvicorn lablens.api:app --host 127.0.0.1 --port 8765` from the repository root. Run the Maven/JAR commands above from `services/workspace`. Python loads the local `.env`; Java needs exported environment variables. The root `compose.yaml` also wires the four services together.

The default bind address is `127.0.0.1`, port `8081`. This is a single-user local prototype without authentication. Binding to another interface is an explicit deployment choice and does not add tenant security. Workspace IDs provide data scoping, not authentication.

## Task admission and overload

Flyway V7 adds a singleton admission guard and a covering workspace/status index. New tasks share a database-wide capacity limit of **1,000 active tasks**, with **200 per workspace** by default. Active means `QUEUED`, `RUNNING` or `CANCEL_REQUESTED`; requesting cancellation of a running task does not release its slot until acknowledgement. Successful/failed/cancelled terminal tasks release capacity. Lowering a limit does not evict existing tasks; new submissions remain rejected until enough tasks drain.

The normal HTTP path checks committed `(workspace_id, idempotency_key)` history first, obtains a Redis decision without holding a database transaction, then starts a `READ_COMMITTED` transaction and locks **global guard → workspace → conversation**. It checks idempotency again before capacity and inserts the task plus optional outbox event atomically. Active counts use indexed, capped reads, so concurrent submissions in different workspaces cannot independently consume the last global slot. Internal callers that already hold a transaction must explicitly use `READ_COMMITTED` and must not pre-acquire resource locks before calling submission; this joined path is not a claim that Redis always runs outside every caller's transaction.

Redis applies a per-workspace 60-second fixed window to **new submission attempts before database admission**, not accepted task count or model tokens. Committed matching-key replays skip Redis and still return `200` when the queue is full or Redis is unavailable; a conflicting payload returns `409`. Racing copies of a key that has not committed may consume multiple attempts, and later database rejection does not refund attempts. If Redis rejects, the server checks again for a concurrently committed matching key before returning the rejection.

With `LABLENS_RATE_LIMIT>0`, a failed Redis gate rejects new provider-capable requests (`agent`, `live`, `dense` or `hybrid`). Offline BM25/extractive can continue through MySQL capacity checks. Cache failures still degrade to a miss. Setting `LABLENS_RATE_LIMIT<=0` explicitly disables the rate gate, including its provider failure rejection; database capacity remains enforced. Already accepted tasks keep their existing execution policy.

| Admission rejection | HTTP | `Retry-After` |
| --- | --- | --- |
| `global_queue_full` | 503 | 1 second |
| `queue_full` | 429 | 1 second |
| `rate_limited` | 429 | 60 seconds |
| `admission_unavailable` | 503 | 1 second |

These responses include `retryable=true` and `retry_after_seconds`. A rejected attempt inserts no task/outbox row and does not advance conversation state. Retry conservatively using the same request and key; the delay is a retry hint, not a completion guarantee.

`GET /api/system/status` exposes `admission.global_pending_limit`, `workspace_pending_limit`, `pending_global`, `pending_global_capped` and `oldest_queued_age_ms`. The active count saturates at global limit + 1; when capped, it is a lower bound. Queue age is sampled separately. `observed_pending_high_water` is a process-local observation from successful submissions and status samples; it resets on restart and is not a continuous database-wide peak. `lablens.admission.outcomes` distinguishes accepted, reused and rejected requests by fixed reason; accepted counts are recorded only after commit. Existing whole-history `task_counts` are separate and remain unbounded aggregate queries.

Every JVM sharing a database must use the same capacity configuration. This guard bounds active task rows; worker concurrency remains per JVM, and the guard does not implement shared provider concurrency, token/cost quotas, tenant fairness or distributed cancellation. See the [Round12 protocol](../../docs/evaluations/conversation-round12/protocol.md) for the bounded, zero-model acceptance plan and the [12-round completion plan](../../docs/finalization-12-rounds.md) for remaining scope.

## Optional RabbitMQ notifications

RabbitMQ is **off by default**. Flyway V6 adds outbox/inbox tables on upgrade; when MQ is enabled, submission writes a `task.available.v1` event in the same MySQL transaction as its task. Replaying an existing idempotency key creates neither a new task nor a new event. The message contains only immutable references; document bodies and questions remain in the saved MySQL snapshot.

From the repository root, set the existing database secrets and a local `LABLENS_MQ_PASSWORD` in the untracked `.env`, then use the optional overlay:

```sh
docker compose -f compose.yaml -f compose.mq.yaml up --build -d
docker compose -f compose.yaml -f compose.mq.yaml ps
```

The overlay adds a single RabbitMQ node with persistent storage, binds AMQP to local port 5673, and enables MQ on the workspace service. It does not expose a management UI or establish multi-node availability. The original `docker compose -f compose.yaml up --build` remains the mode without RabbitMQ.

For a manually launched JAR, first provision a broker user and `lablens` vhost, then export `LABLENS_MQ_ENABLED=true`, `LABLENS_MQ_HOST=127.0.0.1`, `LABLENS_MQ_PORT=5673`, `LABLENS_MQ_USERNAME=lablens`, `LABLENS_MQ_VHOST=lablens` and the password through the local secret mechanism. Start the JAR with the MySQL/Redis/worker configuration above. Do not reuse the main database or vhost for destructive fault tests.

| Variable | Default / purpose |
| --- | --- |
| `LABLENS_MQ_ENABLED` | `false`; controls event capture and transport beans |
| `LABLENS_MQ_HOST` / `LABLENS_MQ_PORT` | `127.0.0.1` / `5673` for manual startup; Compose uses `rabbitmq:5672` |
| `LABLENS_MQ_USERNAME` / `LABLENS_MQ_PASSWORD` / `LABLENS_MQ_VHOST` | `lablens` / empty / `lablens` |
| `LABLENS_MQ_EXCHANGE` / `LABLENS_MQ_QUEUE` | Both `lablens.tasks.v1` |
| `LABLENS_MQ_ROUTING_KEY` | `task.available.v1` |
| `LABLENS_MQ_DEAD_LETTER_EXCHANGE` / `LABLENS_MQ_DEAD_LETTER_QUEUE` | Both `lablens.tasks.dead.v1` |
| `LABLENS_MQ_MAX_QUEUE_LENGTH` | `10000`; main/DLQ also receive a byte limit of this value × 4096; quorum in-flight overshoot remains possible |
| `LABLENS_MQ_PREFETCH` | `8`, allowed 1–32; one consumer |
| `LABLENS_MQ_RELAY_POLL_MS` / `LABLENS_MQ_BATCH_SIZE` | `500` / `16`; one relay thread, coalesced wakeups |
| `LABLENS_MQ_CONFIRM_TIMEOUT_MS` / `LABLENS_MQ_LEASE_SECONDS` | `2000` / `15`; confirm wait is not a whole-operation socket/DB deadline |
| `LABLENS_MQ_MAX_ATTEMPTS` | `5`; persisted exponential retry delay capped at 60 seconds, then `DEAD` |
| `LABLENS_MQ_DATABASE_ATTEMPTS` / `LABLENS_MQ_DATABASE_BACKOFF_MS` | `3` / `250`; bounded inbox database retries, then reject to dead-letter routing |
| `LABLENS_MQ_RELAY_ENABLED` / `LABLENS_MQ_CONSUMER_AUTO_STARTUP` | Both `true`; disable only for controlled component tests/maintenance |

The relay releases its MySQL claim transaction before contacting RabbitMQ. Publisher confirms plus mandatory returns distinguish a broker ACK from a routable delivery; an independent publisher connection avoids sharing a blocked publishing connection with the consumer. Reclaimed events receive a fresh lease token, so a late confirmation from an expired attempt cannot overwrite the current owner.

The consumer checks all message fields against the saved event and task, commits one inbox receipt, then wakes the existing dispatcher and ACKs. `ACCEPTED` means the task was queued; `DUPLICATE` means an identical receipt already exists; `NOT_QUEUED` includes running, cancelling and terminal tasks. None of these paths reset a task or directly call an LLM. At-least-once notification delivery with application deduplication is the intended contract, not end-to-end exactly-once execution.

### Recovery and DEAD diagnosis

Keep `LABLENS_DISPATCH_POLL_ENABLED=true` outside controlled tests. MySQL remains the durable queue: an already running service can continue claiming tasks if notification delivery fails, and polling repairs the gap between an inbox commit and a lost in-process wakeup. A missing broker can delay MQ-enabled startup; to operate without it, explicitly disable MQ while retaining the database dispatcher. The Compose overlay forces MQ on, so use the base Compose file or an explicit final override for that mode. Disabling MQ does not delete existing outbox/inbox records and does not backfill events for tasks submitted while it was off.

Use `GET /api/system/status` to inspect `mq.outbox_counts`, `inbox_counts`, recent records and `oldest_pending_age_seconds`. Counts saturate at 1,001 with `capped=true`; recent lists are limited to 100. `broker_connection=not_checked` describes the database audit view, not verified broker health. Process metrics under `lablens.mq.*` distinguish publish attempts/confirmations/failures, stale confirms, receipts and rejected messages; they reset on restart.

| Symptom | Investigation and safe next step |
| --- | --- |
| `PENDING` / `RETRY` grows | Check MQ enablement, relay startup, credentials/vhost, broker reachability, routing and last error; correct the cause and allow scheduled delivery within the remaining attempt limit. |
| `SENDING` after a lost publisher | Inspect its lease and attempt count; a new owner may claim after expiry. Do not manually reset the task or assume an absent confirm means the broker never received it. |
| `DEAD` | The notification exhausted its attempt limit. Compare outbox, inbox and current task state before any operator action; this version has no automatic redrive endpoint. A task may already have finished through database polling. |
| Consumer database retries exhaust / invalid message | Inspect the configured dead-letter queue and rejection reason; restore the database or correct the producer. Preserve the envelope/event ID for reconciliation. ACK loss may cause an expected duplicate. |
| `406 PRECONDITION_FAILED` during declaration | Existing queue arguments differ from this version. Preserve the existing queue and messages; inspect type, length/byte limits and DLX arguments before a planned drain or migration. Do not delete the queue or ignore the error just to start the service. |

An actual iteration failure exposed the last case: a previously declared queue lacked the new `x-max-length-bytes` argument, and strict declaration correctly rejected reuse. Queue names and their declaration arguments are a deployment contract. For migration, select new versioned exchange/queue and dead-letter names, account for the old queue, drain/retain its messages as appropriate, and only remove old resources after reconciliation. Exporting the variables above works for manual JAR startup; Compose needs those values explicitly passed into the workspace container, via the shipped overlay or an additional local override. A project `.env` entry alone is not container environment injection.

Outbox and inbox audit rows have the lifetime of their task (`ON DELETE CASCADE`). Current application APIs do not delete tasks; future archival/deletion needs an explicit retention policy. A single-node quorum queue, bounded synthetic tests and a working fallback do not prove multi-node failover, production throughput or model answer quality. See the [Round11 protocol](../../docs/evaluations/conversation-round11/protocol.md) and [design review](../../docs/evaluations/conversation-round11/design-review.md) for test scope and known limits.

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
| POST `/workspaces/{id}/tasks` | Required `Idempotency-Key`; `{question,strategy,mode,top_k,approved_revision?}` → task, 202 new / 200 reused |
| GET `/workspaces/{id}/tasks` | Legacy no-parameter response `{tasks:[...]}`; most recent 100 complete task records |
| GET `/workspaces/{id}/tasks?view=summary&limit=25&cursor=...` | Bounded summary page, `has_more` and nullable `next_cursor`; cursor omitted for the first page |
| GET `/workspaces/{id}/tasks/{taskId}` | Task including persisted result, error and timing |
| POST `/workspaces/{id}/tasks/{taskId}/cancel` | No key required; 202 while stopping, 200 if queued cancellation or already terminal; returns saved task |
| GET `/workspaces/{id}/conversations` | `{conversations:[...]}`; latest 100 conversation summaries |
| POST `/workspaces/{id}/conversations` | `{title?,goal?,constraints?,focus?}` → 201 conversation with revision 0 and empty turns |
| GET `/workspaces/{id}/conversations/{conversationId}` | Conversation, pinned state and latest 100 turns in ascending sequence; `omitted_turns` counts older records |
| POST `/workspaces/{id}/conversations/{conversationId}/turns` | Required `Idempotency-Key`; normal task fields + `expected_conversation_revision` + optional `state` patch → task, 202 new / 200 reused |
| GET `/system/status` | MySQL counts, bounded global admission view, MQ audit state, Redis state, process counters/timers, JVM and worker configuration |

Strategies: `bm25`, `dense`, `hybrid`. Modes: `extractive`, `live`, `agent`. Defaults: hybrid / extractive / top_k 5. Validation rejects unknown JSON fields, invalid enum values, top_k outside 1–10, questions over 2000 characters, documents over 20000 characters, more than 100 current documents or 200000 current document text characters per workspace. Document IDs and idempotency keys contain 1–128 ASCII letters, digits, dots, underscores, colons or hyphens and start with a letter or digit. URL-encode IDs in path segments. Error objects contain `error` and `message`.

Provider-capable requests (`live`/`agent`, or `dense`/`hybrid`) require `approved_revision` matching the revision returned by `GET /workspaces/{id}/documents`. The server checks consent under the same workspace row lock used to capture the snapshot; missing consent returns 409 `approval_required`, and a changed revision returns 409 `stale_approval`. Refresh documents and explicitly renew consent before a new submission. Consent revision participates in the idempotency hash. Replaying an existing key with its original payload still returns the original task after later document edits, because it starts no new provider work. Offline BM25/extractive requests do not require this field. The browser binds its checkbox to the displayed revision and clears it on changes and stale responses.

Task fields include `id`, `workspace_id`, `request`, `request_hash`, `snapshot_revision`, `snapshot_document_count`, `status`, `result`, `cache_hit`, `execution_usage`, `usage_unknown`, `error_code`, `error_message`, `error_result`, `worker_ms`, `created_at`, `started_at`, `finished_at`, `cancel_requested_at`, `cancel_acknowledged_at`. POST additionally returns `reused`. Omitted or blank document source labels normalize to `document:<id>`, including legacy blank labels when capturing a snapshot.

### Bounded task-history pages

The explicit summary view returns `{view:"summary", tasks:[...], limit:25, has_more:true, next_cursor:"v1.…", ordering:"created_at_desc_id_desc"}`. `limit` defaults to 25 and accepts canonical integers from 1 to 100. Use the returned cursor unchanged for the next page; `next_cursor` is null when no further row was observed. The query returns at most `limit + 1` rows to decide `has_more`, without a total-count query or OFFSET; this return bound alone does not prove how many index entries MySQL visits. Unknown views/parameters, repeated parameters, invalid limits, and malformed or cross-workspace cursors return 400. Calling the original endpoint with no query parameters retains its original response; adding pagination parameters requires `view=summary`.

Each summary contains task/workspace IDs, `question`, `mode`, `strategy`, status, snapshot revision/document count, conversation ID/sequence, cache/unknown-usage flags, worker duration, safe error code/message and lifecycle timestamps. Its SQL selects named columns and extracts three request JSON fields. It does not select or decode snapshot, result, error-result or execution-usage JSON. The persisted `usage_unknown` flag is a summary hint; full usage accounting, exact source excerpts, reports and failure traces still come from `GET /workspaces/{id}/tasks/{taskId}`. The existing legacy full-list and detail endpoints retain their historical response and fallback usage semantics.

Rows are ordered by `(created_at DESC, id DESC)`. A cursor encodes the workspace and the last returned `(created_at,id)` position with version `v1`; date precision, canonical UUIDs, Base64URL form and token length are checked. The next query uses `created_at < boundary OR (created_at = boundary AND id < boundary_id)`, so equal timestamps have an explicit tie-breaker and deleting the boundary row does not invalidate the position. The existing InnoDB `task_workspace(workspace_id,created_at)` secondary index includes the primary key `id`; this change adds no duplicate index. Actual query-plan and workload evidence must accompany performance claims.

This is a live-history traversal, not a transactionally frozen snapshot. Newer tasks appear on a fresh first page and do not shift an existing older-page boundary; backdated inserts, deletion or manual timestamp changes can still change later pages. The token is a location, not a signed authorization token. Workspace filtering is always part of SQL; authentication remains a separate project requirement.

The browser polls a first page of 25 summaries and loads one full task on selection or a selected task's update. Merely leaving history open does not accumulate every new row. After explicitly loading older pages, background refresh updates already displayed IDs and announces newer records without changing the older cursor or dropping loaded rows; “重新载入最近记录” starts again at the newest page. Detail responses are guarded by workspace and selection identity so a slow response cannot replace a later selection. Full report sources, error traces and unknown provider usage remain in the detail panel.

### Persistent conversations

Conversation turns require `mode=agent`; their defaults are agent / BM25 / top_k 5. Independent task defaults stay unchanged. Creating a conversation with `{}` produces an empty state `{goal:"",constraints:[],focus:""}`. Titles allow 160 characters; goal 2000; focus 300; constraints at most 12 nonblank strings of at most 300 characters each. An optional turn `state` merges only supplied non-null fields; `constraints:[]` explicitly clears constraints. State represents user-supplied intent and preferences, never model-certified experiment facts.

Append sends both the document `approved_revision` and the currently displayed `expected_conversation_revision`. The transaction locks global admission guard, workspace, then conversation, checks original-key replay first, rejects outdated conversation revisions with 409 `stale_conversation` and another queued/running/cancellation-pending turn with 409 `conversation_busy`, then captures sources, appends a task and advances conversation revision exactly once. The request hash includes conversation identity, expected revision and state patch. Retrying an uncertain POST must keep the original body and key; a new message requires fresh revisions. These checks serialize one active turn per conversation; independent tasks keep separate histories while sharing database-wide admission capacity.

Each immutable worker snapshot contains `conversation_context` with the accepted 1-based conversation revision, pinned state, at most 20 previous terminal turns and `omitted_turns`. Answers are clipped to 4000 characters in context/history previews; complete saved reports remain available through the task endpoint. Prior answers are untrusted context requiring fresh source retrieval. The service does not write automatic summaries or durable fact memories. Python applies its separately reported context budget without modifying the stored snapshot.

Conversation detail joins current task rows, so completion, failure and clarification appear after reload without an asynchronous summary update. Turn previews include `seq`, `task_id`, `question`, `answer`, `answer_truncated`, task `status`, worker `result_status`, `agent_state`, `snapshot_id` and timestamps. Tasks expose nullable `conversation_id`, `conversation_seq` and `conversation_revision`; independent tasks omit conversation context and retain their behavior. A matching `clarification_required` worker result is a terminal successful interaction awaiting the next user turn; `context_budget_exceeded` is an explicit failed task. Success and structured error metadata must match the immutable conversation ID/revision. Cancellation is described below. Step resume, pause, SSE and cross-session automatic long-term memory remain unimplemented.

### Durable cooperative cancellation

Queued cancellation atomically saves `CANCELLED` with zero provider usage before dispatch. Running cancellation first saves `CANCEL_REQUESTED`; the conversation remains busy until the local worker acknowledges that execution has exited. The dispatcher retries only the control endpoint, never the model task. Repeating a cancel request is idempotent; already completed/failed tasks retain their original outcome. Cancellation intent survives JVM restart and is not cleared by task lease expiry. A late response cannot publish a successful result over either cancellation state.

The Python process checks a shared cancellation signal at provider/tool boundaries. Already-sent upstream requests can finish or time out and may incur cost. Known completed usage is retained; unavailable usage stays unknown. `CANCELLED` means local execution is stopped, not that the remote provider was forcibly interrupted. An unreachable worker leaves `CANCEL_REQUESTED` visible and blocks another turn in that conversation. Cancelled history has no fabricated answer; preparing a retry only fills the original request, and a newly approved submission starts a separate task.

The worker registry is local to one Python process, with bounded tombstones retained until process exit. It rejects new work when full rather than discarding replay protection. This deployment requires one Python worker process at the configured loopback address; it is not a distributed cancellation protocol. Do not run multiple workers or replace a live worker endpoint while old workers remain active.

The cancellation endpoint requires no body or new idempotency key and returns the full task row.

| Previous state | Returned state | HTTP | Meaning |
| --- | --- | --- | --- |
| `QUEUED` | `CANCELLED` | 200 | No worker dispatch or provider usage. |
| `RUNNING` | `CANCEL_REQUESTED` | 202 | Durable intent; waiting for local worker acknowledgement. |
| `CANCEL_REQUESTED` | `CANCEL_REQUESTED` | 202 | Still waiting; the conversation remains busy. |
| `SUCCEEDED`, `FAILED`, `CANCELLED` | Unchanged | 200 | A terminal task is not rewritten or resumed. |

`cancel_requested_at` and `cancel_acknowledged_at` record the two distinct events.

The JVM reconciles requests against its fixed configured worker URL. Loopback retrieval children share the parent cancellation flag and hold their own active reference. Active child work prevents acknowledgement even if its parent HTTP call times out.

The registry rejects new IDs at 10,000 entries rather than evicting tombstones. Restart the local worker after active work has ended to clear the registry; MySQL retains history and cancellation intent.

The controlled fault runner is `scripts/evaluate_cancellation_round2.py`: it uses isolated MySQL, Redis, JVM and Python services plus a scripted loopback provider, with zero external model calls. Its output distinguishes transport controls from semantic answer-quality evaluation.

## Consistency and failure handling

- MySQL is the source of truth. Workspace row locks serialize document changes and snapshot capture. Each task stores the complete immutable request/document snapshot and revision; later edits and deletion cannot change a queued analysis.
- A unique `(workspace_id,idempotency_key)` database constraint backs deduplication. Repeating a key with the same normalized request returns the original task, even after document edits. A different request with that key returns 409. Different keys represent different tasks, including for identical content.
- Claiming uses MySQL `FOR UPDATE SKIP LOCKED`. The default executor permits two active tasks per JVM and has bounded in-memory capacity. MySQL admission caps queued/running/cancellation-pending tasks at 1,000 across the database and 200 per workspace by default. These task-row limits are separate from distributed provider execution or token/cost quotas, which remain unimplemented.
- Committed HTTP submissions and worker completion signal a dedicated dispatch thread. Signals coalesce into at most one waiting wakeup, so HTTP threads do not drain the database queue and bursts do not create an unbounded scheduling backlog. The existing 250 ms poll remains the restart/missed-signal fallback. Fast tasks can start the next queued task immediately after releasing their slot instead of waiting for the next poll.
- The periodic fallback signals that same dispatcher rather than creating a parallel claim loop. Claims use `READ_COMMITTED` to avoid unnecessary range gap locks. Expiry discovers candidate IDs through the `(status,lease_until)` index without locking, then rechecks expiry through primary-key writes; it does not range-update unexpired running tasks. Claim/completion/failure writes retry only rollback-safe database lock conflicts, at most twice with 10/20 ms pauses. These retries never repeat worker HTTP or provider calls.
- Completion requires the same owner, `RUNNING` state and an unexpired lease. A late response cannot overwrite an expired or completed task. Queued tasks survive restart. Expired running tasks become `FAILED`; live/Agent tasks and dense/hybrid extractive tasks record unknown external usage because embeddings can incur provider cost. Unknown request/token counts are null rather than fabricated zeros. There is no automatic model retry after an uncertain outcome.
- Redis caches only successful extractive results, keyed by namespace, workspace, revision and canonical request hash. Live and Agent tasks always bypass the result cache. Concurrent different-key cache misses are not coalesced. The configurable cache namespace must change if worker behavior or embedding configuration changes.
- `execution_usage` records the current task's provider usage. Cache hits have zero current usage and `cache_reused=true`; historical usage is explicitly labelled `origin_usage` on the task and result. `result.usage` also represents zero current usage on a cache hit. Redis lookup hits, validated cache hits and rejected cache entries have separate metrics. Invalid cached payloads are discarded before recomputation.
- A worker HTTP 200 alone is insufficient: the response must have a supported success status, matching mode, snapshot identity and retrieval strategy, and a well-formed usage object. Structured error details can preserve safe, allowlisted usage counters only when they match the task; arbitrary provider error text is never returned. `error_result` and `execution_usage` retain known partial usage from failed calls. A failed Agent retrieval with unreported embedding usage stores null embedding counters, while preserving the known decision-model call count; null never means zero. Successful worker responses still require nonnegative integer counters.
- Redis applies an atomic Lua fixed-window limit to new submission attempts per workspace. Committed idempotent replays skip it; concurrent copies before commit may consume several attempts. With the limiter enabled, Redis failure rejects new provider-capable tasks while offline BM25/extractive still uses MySQL capacity checks. Cache failure degrades to a miss; status/metrics distinguish the policies. Redis is not the durable queue.
- Database unavailability fails HTTP operations with 503. Worker errors/timeouts produce durable task failures and safe error messages; external provider response bodies are not echoed. This service does not infer that an unknown outcome had zero model cost.

## Configuration

| Variable | Default |
| --- | --- |
| `LABLENS_WORKSPACE_BIND` / `LABLENS_WORKSPACE_PORT` | `127.0.0.1` / `8081` |
| `LABLENS_DB_URL` / `LABLENS_DB_USER` / `LABLENS_DB_PASSWORD` | local port 3307 database `lablens_workspace` / `lablens` / empty |
| `LABLENS_DB_POOL_MAX` / `LABLENS_DB_POOL_MIN_IDLE` | 12 / follows pool max; per-JVM Hikari connection budget |
| `LABLENS_DB_ACQUIRE_TIMEOUT_MS` | 3000; maximum Hikari connection acquisition wait, not a SQL statement timeout |
| `LABLENS_HTTP_THREADS_MAX` / `LABLENS_HTTP_THREADS_MIN` | 200 / 10; Tomcat platform thread max / minimum spare |
| `LABLENS_HTTP_MAX_CONNECTIONS` / `LABLENS_HTTP_ACCEPT_COUNT` | 8192 / 100; Tomcat connection limit / socket accept backlog request |
| `LABLENS_REDIS_HOST` / `LABLENS_REDIS_PORT` / `LABLENS_REDIS_PASSWORD` | `127.0.0.1` / `6380` / empty |
| `LABLENS_WORKER_BASE_URL` | `http://127.0.0.1:8765` |
| `LABLENS_WORKER_TIMEOUT_SECONDS` | 90 bare JVM fallback; example and Compose set **150** |
| `LABLENS_WORKERS` | 2 (allowed 1–32) |
| `LABLENS_TASK_LEASE_SECONDS` | 180; must exceed worker timeout |
| `LABLENS_CACHE_TTL_SECONDS` | 300 |
| `LABLENS_CACHE_NAMESPACE` | `conversation-round9-agent-v10` bare JVM fallback; base Compose currently overrides it |
| `LABLENS_RATE_LIMIT` | 120 new submission attempts per workspace per 60-second window; nonpositive explicitly disables the Redis admission gate |
| `LABLENS_MAX_PENDING_TASKS` | 200 active tasks per workspace; allowed 1–100000 |
| `LABLENS_MAX_PENDING_GLOBAL` | 1000 active tasks across the database; allowed 1–100000; all sharing JVMs must agree |
| `LABLENS_DISPATCH_ENABLED` | true; false enables deterministic storage tests |
| `LABLENS_DISPATCH_POLL_ENABLED` / `LABLENS_DISPATCH_POLL_MS` | true / 250; keep enabled for restart and missed-wakeup recovery |
| `RAG_TIMEOUT_SECONDS` / `RAG_TOTAL_SECONDS` (Python) | 45 / 80 bare fallbacks; example and Compose set **60 / 120** |
| `LABLENS_AGENT_TOOL_BASE_URL` (Python) | `http://127.0.0.1:8765`; explicit loopback IP only |
| `LABLENS_AGENT_TOTAL_SECONDS` (Python) | **75**, allowed 1–80; shared by decisions, tools and review |
| `LABLENS_CONTEXT_WINDOW_TOKENS` (Python) | **65536** conservative serialized-byte units including output reserve and margin; not an exact tokenizer count |

Live RAG normally makes a draft and one review call with the same configured model, sharing the total RAG budget. Agent calls instead share its shorter total budget, forwarded to local retrieval requests. Keep the JVM timeout above the chosen Python budget and the task lease above the JVM timeout. These are cooperative deadlines, not cancellation guarantees.

Metrics are available at `/actuator/metrics` and `/actuator/prometheus`. JVM/memory/HTTP metrics are provided by Micrometer; `lablens.*` counters track actual submissions, duplicates, task outcomes, cache decisions, Redis degradation and worker concurrency. Process counters reset on restart; task counts and timings remain persisted in MySQL. No load test, SLA, production throughput or model-accuracy claim follows from simply exposing these metrics.

The resource variables above make the previous effective limits explicit; this iteration does not claim that increasing or reducing those defaults improves throughput. Each JVM owns its own pool and worker budget, so adding replicas also increases possible database connections and worker concurrency. Keep minimum idle no greater than pool max, minimum spare HTTP threads no greater than HTTP max, and leave capacity for database maintenance and other services. Tomcat MBean registration is enabled to expose its running thread gauges. Socket backlog acceptance is also subject to operating-system limits.

`GET /api/system/status` includes `resources.database_pool` (`active`, `idle`, `pending`, `total`, `max`, `min_idle`, `acquire_timeout_ms`) and `resources.http` (configured connector budgets). The resource snapshot reads Hikari's local pool bean without acquiring a database connection or scanning tasks. The **existing** overall status endpoint still aggregates task/workspace counts; that query cost is not solved by this snapshot and belongs to the later data-scale round. Point-in-time fields are not a continuous peak measurement.

| Metric | Interpretation |
| --- | --- |
| `hikaricp.connections.active`, `.idle`, `.pending`, `.max` | Pool connections and callers waiting for a connection, by pool |
| `hikaricp.connections.acquire` | Connection acquisition timer, including real pool wait; not admission guard wait |
| `tomcat.threads.busy`, `.current`, `.config.max` | Running connector thread observations and configured maximum, by connector |
| `lablens.admission.guard.inflight` | Calls currently executing the guard statement; includes statements that acquire the row immediately |
| `lablens.admission.guard.wait` (`result=acquired/error`) | Elapsed guard statement call, including SQL roundtrip and lock wait; not a pure MySQL lock-wait statistic |
| `lablens.admission.transaction` (`completion=committed/rolled_back/unknown`) | Submission transaction callback entry through actual completion, including guard wait and commit/rollback. Excludes earlier Redis and connection acquisition; joining an outer `READ_COMMITTED` transaction includes its later caller work until actual completion. Replays returned before entering the transaction and Redis rejection do not increment this timer. |
| `lablens.executor.active`, `.queued`, `.pool.max`, `.queue.capacity` (`executor=worker/dispatch/cancellation`) | Bounded executor use and limits. These count local runnable work, not all persisted queued tasks. |

Timers export count, total and max; no latency percentile is inferred from an average. The singleton admission row intentionally serializes new submissions across workspaces and can become a contention hotspot. This version records that cost before attempting to alter the consistency design. Correlate guard timing, pool pending/acquire, HTTP latency, worker activity and the durable queue before attributing a bottleneck; controlled offline worker throughput does not measure LLM throughput.

For direct JAR startup, export admission and resource variables into the Java process; `.env` is not automatically read by Java. The base Compose file explicitly forwards the admission and resource variables above from the Compose environment or project `.env`. Other `.env` entries only affect a container when its configuration passes them through. Inspect the final Compose configuration and recreate the workspace service when changing its limits.

## Tests

```sh
# Pure JVM tests; real-service integration tests are explicitly skipped.
mvn test

# With MySQL + Redis and the environment above configured:
LABLENS_INTEGRATION_TESTS=true mvn test

# With an isolated real RabbitMQ vhost as well:
LABLENS_INTEGRATION_TESTS=true LABLENS_MQ_INTEGRATION_TESTS=true mvn test
```

Integration tests use genuine MySQL and Redis (no H2 substitution), create UUID-scoped fixtures and remove their database rows afterward. They cover concurrent idempotency, conflicts, immutable snapshots, document versioning/deletion, workspace scoping, capacity rollback, lease expiration, stale-owner completion, real cache revision separation/TTL and HTTP validation. They disable automatic dispatch and do not call an LLM. Outbox/inbox integration tests additionally cover rollback, duplicate submissions/receipts, publisher lease fencing, persisted backoff and non-queued task preservation. `TaskMqIntegrationTest` is separately gated by `LABLENS_MQ_INTEGRATION_TESTS`; it uses genuine RabbitMQ and controlled local business execution, with no external LLM calls. Skipped tests are not evidence of broker verification. Cross-service RAG/load evaluations are separate evidence in the repository evaluation reports.

`AdmissionIntegrationTest` requires an isolated database with no other active service. Its 12 cases cover cross-workspace competition, slot retention/release, rollback, visible commits after waiting for the global guard, overload HTTP contracts, conversation consistency and idempotent replay. Redis delay/failure decisions in several cases are controlled injections around real MySQL transactions; these cases do not alone prove a real Redis outage. Runtime fault and open-arrival load evidence must be reported separately.

## UI failure and retry controls

Failed tasks expose saved current usage and safe error details even when no result exists. Preparing a retry restores the question and modes, clears the previous intent and model consent, and sends no request. The next manual submission creates a new key using current documents; the original failed task remains unchanged. Replaying an uncertain submission continues to use its existing key.

Run UI state regressions with `node --test services/workspace/tests/workspace-ui.test.cjs` (Node22 in CI). These DOM simulation checks are distinct from the recorded real Chromium recovery flow and from model quality evaluation.

## Workspace Agent branch

`mode=agent` runs the existing Python `Agent.run` workspace-context branch. The same branch is available through `/api/run` with `{question,workspace_context:{snapshot_id,documents,strategy,top_k,conversation_context?}}`; Java task submission remains the durable history path. The live HTTP model selects one structured JSON action from `search_workspace`, `read_source_passages`, `request_clarification` and `verify_and_submit_report`. The legacy/scripted adapter keeps native `verify_sources` and `submit_report`. Search executes a real HTTP request to the local `/api/rag/answer` endpoint, with the original immutable snapshot injected by the server. It can choose BM25 or the approved request strategy, and cannot supply a URL or replace documents. The combined `verify_and_submit_report` action deterministically verifies every observed chunk before final same-model review; its separate `source_validation` trace explicitly identifies server verification, not an additional model-selected step. Contrary retrieved evidence remains included in review. The report tool selects passage IDs from a server-provided catalog; the server copies the exact source quotations and rejects unknown IDs before review. With no search hits, the model may submit an insufficient assessment; this verifies no source and does not prove the fact is absent. The legacy adapter may explicitly record empty evidence with `verify_sources([])`. Clarification ends the turn without requiring evidence. Live decisions request a strict JSON action schema, then locally validate the returned shape, selected action and arguments. Missing/malformed/truncated output stops without an automatic fallback request. This replaces the earlier native `tool_choice=required` path that failed in recorded trials. Rejected decisions retain safe shape metadata without publishing unaccepted model content.

Reports preserve `assessments`, `evidence_verdict`, source citations and `agent.trace` in `result_json`. Reloading the task through `GET /workspaces/{id}/tasks/{taskId}` reads this saved report; later document edits do not regenerate or replace it. Agent errors preserve a bounded, snapshot-matching trace in `error_result.agent`; step exhaustion, timeouts, tool failures and insufficient evidence terminate explicitly. Live and Agent modes bypass Redis result caching. The shared UI consent checkbox applies to Agent mode even with BM25.

The Python worker uses `LABLENS_AGENT_TOOL_BASE_URL=http://127.0.0.1:8765` by default. Override the port when running an isolated worker; only explicit loopback-IP HTTP URLs are accepted. `LABLENS_AGENT_TOTAL_SECONDS` defaults to 75, configurable from 1 to 80. Default limits remain six model decision steps, eight tool calls and one mandatory same-model report review. The live path reserves up to 20 seconds of the shared 75-second budget for review and bounds each decision by the configured model timeout and remaining budget after the review reserve; these are deadlines, not latency guarantees. It normally needs two decisions plus review for a single search/report, but may choose additional retrieval or clarification. Provider context omits duplicated hit text while raw traces retain evidence. Reports append saved user scope as attributed user input, never as verified source conclusions; semantic constraint compliance is evaluated separately. Remaining time is forwarded to retrieval as `X-LabLens-Budget-Seconds`; this cooperative budget stops subsequent calls and does not promise hard cancellation of an in-flight provider request.


Drafting and review by the same model are not independent semantic judgments. Exact passage selection and quote checks establish source existence, not entailment or source authenticity. `supported` / `contradicted` / `insufficient` remain bounded model assessments and require review for consequential claims. The [DPO investigation case](../../docs/cases/dpo-preference-generation.md) separates source/configuration evidence, archived runtime observations and unsupported causal explanations.

A [real Agent timeout control](../../docs/evaluations/v0.13/agent-fault/README.md) follows JVM → actual Python Agent → scripted local provider. Both decision and embedding-tool timeouts preserve task history, trace and known/unknown usage through refresh and direct MySQL reads. The [reproduction harness](../../scripts/evaluate_workspace_agent_fault.py) uses isolated ports/database and no external model, stops its processes and retains evidence. These controls verify failure handling, not model quality; existing [v0.12 measurements](../../docs/v0.12-evaluation.md) keep their original scope and failures.

The [Round10 model diagnostic](../../docs/conversation-round10.md) retained four timed-out direct reviews with unknown usage and no accepted report. Round11 notification reliability does not alter those results or demonstrate an improvement in model delivery or answer correctness.

## Bounded imports and persistent retrieval (Round15 candidate)

`POST /api/workspaces/{id}/imports` accepts `Idempotency-Key` and `{ "expected_revision": 0, "documents": [{"id":"notes","title":"Notes","text":"Evidence","source":"notes.md"}] }`. The limits are 25 documents, 20,000 UTF-16 characters per document, 100,000 per batch, and a 1 MiB serialized request. The workspace remains capped at 100 documents / 200,000 characters. New submissions return 202; identical retries return 200 with the same batch. A changed body with the same key conflicts.

`GET /api/workspaces/{id}/imports/{batch}` returns durable per-item outcomes. `POST .../{batch}/cancel` cancels a queued batch; it cannot undo an applied transaction. Applying all documents, version rows, the single workspace revision and APPLIED result uses one MySQL transaction. A stale revision or capacity violation yields FAILED without partial documents. Permanent SQL constraints or malformed stored payloads are terminal, so they do not block later batches. Transient SQL errors roll back and remain queued. This import queue polls MySQL; it does **not** extend the task-only MQ envelope.

Import dispatcher property `lablens.import-dispatch-enabled` follows `lablens.dispatch-enabled` by default, with `lablens.import-poll-ms=500`. Pending defaults are 4 per workspace / 64 global (`lablens.import-max-pending-workspace`, `lablens.import-max-pending-global`); all JVMs sharing the database should agree. Import status always says `index_status=not_checked` because saving files does not build an index.

Set Python `LABLENS_INDEX_DB` to a private writable file (persistent volume in containers). `LABLENS_INDEX_MAX_ENTRIES` defaults to 128 identities; the implementation also bounds each artifact to 16 MiB and the database to 128 MiB. SQLite stores derived immutable chunks and normalized vectors only. MySQL owns original text, revisions and task history. There is no ANN engine, automatic eviction, multi-host artifact replication or unlimited corpus claim. BM25 still computes term scoring at query time.

`GET /api/workspaces/{id}/index?strategy=bm25` checks the exact current snapshot. `POST .../index` with `{ "strategy":"bm25", "expected_revision":1 }` builds without external calls. Dense/hybrid additionally require `approved_revision:1` and the configured embedding provider. Both share a semantic artifact. Direct worker routes are `/api/rag/indexes/build` and `/status`, with `{snapshot_id,documents,strategy}`. The existing Agent retrieval tool reuses exact matching READY artifacts and records their IDs; current tasks still contain full immutable document snapshots.

Identity includes snapshot, ordered document content, chunk format and embedding provider/model/revision/credential scope. An edit, deletion or changed model revision misses the previous identity. READY publication is atomic; a damaged artifact is quarantined. A worker stopped mid-build remains BUILDING until its finite lease expires, then FAILED; potentially billed usage is unknown. Repeating a failed identity never rebuilds automatically. A deliberate retry currently needs a new snapshot identity; artifact capacity is operator-managed. Request-local retrieval remains available when no matching index exists, but failed/in-progress semantic artifacts block accidental document re-embedding. Local BM25 can fall back without provider costs.

Index calls release the DB transaction before contacting the worker. Java permits two concurrent proxy calls, validates returned snapshot/content identity and marks a result stale if documents changed during a build. A timeout means outcome unknown: inspect status before retrying. Builds are synchronous bounded calls with durable worker state, not a second Java/MQ job queue. Current UI exposes only free BM25 builds; semantic builds are explicit API operations.

The candidate is loopback-only and single-user. Authentication/authorization is the next planned round. For measured engineering/retrieval outcomes and exact limitations see [Round15](../../docs/conversation-round15.md).
