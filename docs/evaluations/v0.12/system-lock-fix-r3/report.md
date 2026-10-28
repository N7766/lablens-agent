# JVM / MySQL / Redis system evaluation

Real HTTP service controls with explicitly synthetic documents and BM25/extractive tasks. No model calls, database mocks, direct database operations, restart or cleanup in this evaluator.

Run: 0b1f4ce6-68b7-4e8d-90dd-a6acc37137e4
Checks: 13/13

| Check | Passed |
| --- | --- |
| 16 concurrent identical keys create exactly one task | True |
| Deduplicated workspace has exactly one persisted task | True |
| Reusing key with changed request returns 409 | True |
| Cross-workspace task and document lookup returns 404 | True |
| Completed old result remains byte-equivalent after update and deletion | True |
| Original key retains original snapshot after document changes | True |
| New task uses current version and excludes deleted document | True |
| Same snapshot/request with different key reuses Redis result | True |
| Redis hit reports zero current model and embedding calls | True |
| All distinct load tasks complete successfully | True |
| Distinct load requests bypass result cache | True |
| Load makes zero model or embedding calls | True |
| Configured two-worker bound is respected | True |

## Local closed-loop workload

32/32 succeeded; 172.183836 observed successful tasks/s over 0.185848 seconds.
Client end-to-end p50/p95: 20.749 / 127.951 ms.
Persisted queue p50/p95: 1.549 / 3.995 ms.

These numbers include submission and polling overhead, a tiny synthetic corpus, loopback networking and a single local JVM. They do not measure maximum throughput, a production SLA, LLM serving capacity, or independent-user activity.

Every observed HTTP response, including polling and metrics, is retained in observations.jsonl. Workspaces and task history are retained for restart checks.
