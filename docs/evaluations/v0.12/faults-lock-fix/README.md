# Fault regression after the database locking fix

Run `d9521f8e` on 2026-10-28 repeated the **unchanged eight-case process-fault
protocol** against the final database-locking fix. **8/8 cases and 37/37 assertions
passed.** Earlier evidence remains in `../faults/` and `../faults-event-wake/`.
Repeated regression runs are not additional independent model-quality samples.

Tested JAR SHA256:
`443a6ce757b5aebda839148acc0d6f1c33183b888970ad790e3fe6b2600d95cc`.
The inventory in `summary.json` also includes Java sources, configuration and
Flyway migrations, including V3. Post-run verification confirmed that all
recorded implementation/JAR hashes still matched.

| Actual process control | Result |
|---|---|
| Queue a task with dispatch disabled, stop JVM, restart with dispatch enabled | Same task completed once |
| SIGKILL JVM while a hybrid/extractive request is RUNNING; restart with another task queued | Expired running task FAILED with unknown provider usage and no observed automatic retry; queued task succeeded |
| Controlled worker returns malformed HTTP 200 JSON | FAILED with `worker_invalid_result` |
| Controlled worker returns structured HTTP 503 | FAILED; provider calls remained known zero |
| Controlled worker returns structured HTTP 502 with unknown embedding usage | FAILED; reported attempt and token uncertainty preserved |
| Controlled worker sleeps five seconds against the actual two-second Java HTTP timeout | FAILED at 2,003 ms; embedding-capable task usage remained unknown |
| Stop real Redis, replay one idempotency key, then restart Redis | MySQL reused the same task; execution completed once; Redis availability recovered |
| Two unique submissions, a duplicate and a third unique submission with real Redis rate limit 2 | Duplicate was free; third unique request returned HTTP 429 and was never dispatched |

This run used three actual JVM processes, real MySQL and a dedicated real Redis
instance. The HTTP worker deliberately supplied deterministic response bodies,
errors and delays: **real LLM calls and real embedding requests were both zero**.
It verifies fault contracts after the locking change; this one-worker experiment
does not itself establish high-concurrency throughput or prove that no lock
contention can occur. Separate load evidence is required for those claims.

The dedicated MySQL database `lablens_faults_d9521f8e` retains ten terminal tasks:
five SUCCEEDED and five deliberately FAILED. `mysql-snapshot.json` records a
direct post-run database query. `worker-events.json` contains the ten controlled
HTTP requests. `summary.json` contains all case assertions, task snapshots,
process events, timings, runtime versions and source hashes.

`verification.json` confirms that owned JVM/Redis PIDs exited, ports 8082 / 8767 /
6391 closed, all task rows were terminal, source/JAR hashes matched, and public
JSON contained zero exact local service-password/key matches. Main services on
8081 / 8765 / 6380 were untouched. Private logs remain in
`.local/faults/d9521f8e/`; the dedicated database was intentionally retained.

To repeat into a new directory after building the JAR:

```bash
.venv/bin/python scripts/evaluate_workspace_faults.py --output reports/workspace-faults-new-run
```

The script creates an isolated database and processes, writes a summary and
worker event log, and stops only the processes it created. The separate database
and source-hash verification in this folder belongs to the recorded run. See
`../faults/README.md` for environment requirements and full protocol limitations.
