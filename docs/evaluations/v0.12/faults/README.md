# Workspace process-fault controls

Run `a09d5e64` completed on 2026-10-28: **8/8 cases, 37/37 assertions passed**.
This was a real Java process / MySQL / Redis experiment with a deliberately
controlled HTTP worker. It made **zero real LLM calls and zero real embedding
requests**. Worker responses, latency and reported provider errors were injected;
these results are not RAG quality, provider reliability or model performance data.

## What actually ran

- OpenJDK 24.0.2, MySQL 9.5.0 and Redis 8.2.2; exact version strings and source/JAR
  SHA256 values are in `summary.json`.
- Three distinct JVM processes, including one `SIGKILL` while a task was RUNNING.
- Dedicated JVM port 8082, worker port 8767 and Redis port 6391. The normal
  application on 8081, Python worker on 8765 and Redis on 6380 were untouched.
- A new persistent database, `lablens_faults_a09d5e64`; no existing database was
  cleared. The dedicated database remains available locally as evidence.
- One Java worker slot, a 2-second worker HTTP timeout, a 3-second running-task
  lease and a Redis submission limit of two unique requests per minute.
- Ten received controlled-worker HTTP requests. All ten persisted tasks reached
  terminal states: five SUCCEEDED and five deliberately FAILED.

## Results and interpretation

| Case | Observed result | Meaning |
|---|---|---|
| Enqueue with dispatch disabled, stop and restart JVM | Same persisted task completed; one worker request | QUEUED work survives an actual process restart |
| Kill JVM during a slow hybrid/extractive task; another task remains queued | Running lease expired to FAILED with unknown provider usage; queued task completed; interrupted task was received once | Restart preserves queued work and avoids automatically replaying a possibly billable interrupted request |
| Worker returns HTTP 200 with malformed result structure | FAILED with `worker_invalid_result` | HTTP success alone is insufficient to persist a successful analysis |
| Worker returns structured HTTP 503 | FAILED with `rag_unavailable`; provider counts remain known zero | Missing-provider configuration is distinguished from a potentially billable failed call |
| Worker returns structured HTTP 502 with partial unknown embedding usage | FAILED; one reported embedding attempt and unknown tokens preserved | Failure metadata survives the HTTP/JVM/MySQL boundary; this reported attempt is simulated |
| Worker sleeps 5 seconds, exceeding the actual JVM HTTP timeout | FAILED with `worker_timeout` after 2,003 ms; hybrid/extractive provider usage unknown | Embedding-only modes also require conservative accounting after transport failure |
| Stop Redis, submit and replay the same idempotency key, restart Redis | Degraded status observed; same MySQL task reused and completed once; Redis recovered | Redis acceleration/rate limiting can fail open while durable MySQL idempotency remains authoritative |
| Two unique requests plus a duplicate and a third unique request | Duplicate reused the first task; second unique accepted; third unique returned HTTP 429 and never reached the worker | The real atomic Redis limiter excludes idempotent replays |

The no-retry observation covers restart, lease expiry, completion of the queued
task and an additional one second. It is a bounded observation supported by the
recorded worker request log, not an assertion about indefinite future execution.

No timing here represents model generation latency. In particular, the 2,003 ms
measurement exercises the deliberately configured two-second transport timeout.
A local timeout or process kill cannot establish cancellation of a request that
has already reached an external model provider.

## Evidence files

- `summary.json`: configuration, cases/assertions, task snapshots, process events,
  final system metrics, version strings and source/JAR hashes.
- `worker-events.json`: every controlled request and response/disconnection result.
- `mysql-snapshot.json`: a direct post-run query of the retained database's ten
  task rows, including statuses and persisted usage.
- `verification.json`: independent post-run checks that dedicated ports were
  closed, owned PIDs exited, source hashes still matched, and JSON artifacts
  contained zero exact matches to the local service password/key values.

Private stdout logs remain under `.local/faults/a09d5e64/` and are not published.
Only processes created by this experiment were stopped.

## Repeat

After building the workspace JAR and starting the isolated local MySQL service,
run the script with a fresh output directory:

```bash
.venv/bin/python scripts/evaluate_workspace_faults.py --output reports/workspace-faults-new-run
```

The script reads `.local/services.env` without printing credentials, creates a new
database, starts dedicated Redis/JVM/controlled-worker instances, runs all eight
cases, writes `summary.json` and `worker-events.json`, and stops its own processes.
The dedicated ports must be free. Use command-line overrides for Java, MySQL,
Redis binary paths, the socket, JAR and ports on another machine. The separate
post-run database/hash checks above belong to the recorded run and are not
automatically regenerated by the experiment script.
