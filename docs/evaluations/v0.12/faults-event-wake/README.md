# Fault regression after event-driven dispatcher wake-up

Run `11b106ec` on 2026-10-28 repeated the unchanged process-fault protocol after
the dispatcher began waking immediately after committed submissions and completed
tasks. The 250 ms scheduled fallback remains in the implementation.

**All 8 cases and 37 assertions passed again.** This is a required regression of
the scheduler change, not eight additional independent product-quality tasks.
The earlier run and its artifacts remain in `../faults/`.

The tested JAR SHA256 was
`f49f866e418ceaf5602463e395b70464f3f27ebfcc954ecb05fca8baf4521591`.
`summary.json` records the complete implementation/JAR hashes, exact process
versions, execution timestamps, task results and assertion details.

The unchanged controls cover queued-task recovery across an actual JVM restart,
SIGKILL during RUNNING and lease expiry without automatic retry, malformed HTTP
200, structured HTTP 503/502 accounting, actual Java HTTP timeout, real Redis
outage/recovery with MySQL idempotency, and the atomic Redis rate limit.

This run used three actual JVM process instances and a dedicated Redis instance
on ports 8082 / 6391, plus a deliberately controlled HTTP worker on 8767. The
retained database is `lablens_faults_11b106ec`. Ten tasks were persisted: five
SUCCEEDED and five deliberately FAILED. **Real LLM and embedding calls: zero.**
Injected response bodies and latency test transport/failure behavior, not RAG
ranking, generation quality or provider speed.

`verification.json` confirms that dedicated ports are closed, all owned PIDs have
exited, implementation/JAR hashes still match, all ten directly queried MySQL
tasks are terminal, and published JSON contains no exact service-password/key
matches. `mysql-snapshot.json` preserves that direct database query;
`worker-events.json` records the ten controlled HTTP requests. Private logs remain
under `.local/faults/11b106ec/`. No main service or preexisting database was stopped
or cleared.

Reproduce into a new directory:

```bash
.venv/bin/python scripts/evaluate_workspace_faults.py --output reports/workspace-faults-new-run
```

The script creates its own database and processes and writes the run summary and
worker events. The separate post-run database/hash verification in this folder
belongs to this recorded run. See `../faults/README.md` for the complete protocol,
interpretation and limitations.
