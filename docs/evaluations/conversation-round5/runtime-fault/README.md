# Round5 controlled transport regression

Reused `scripts/evaluate_runtime_round4.py` unchanged against the Round5 source/JAR. The isolated run uses a real JVM API, MySQL, Python Agent and HTTP clients with a scripted loopback provider. Fast success and review timeout passed **64/64 checks**. External model/embedding requests: **0**.

`summary.json` records source and JAR hashes, durable SQL/API agreement, timeline pairs, request bytes, idempotent replay, unknown usage and cleanup. The test budget is 6 seconds with an 8-second scripted review delay. Response text and usage are synthetic; this does not demonstrate model quality, throughput or production uptime. Database names retain the reused harness's `round4_runtime` prefix; this is a fresh isolated run, not reused Round4 output. All owned test processes stopped; the isolated database and logs remain for inspection.
