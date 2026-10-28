# Workspace Agent timeout controls

Run `d5997043` used a real JVM service, real Python `Agent.run` workspace branch, real MySQL and real Redis. A deliberately scripted local OpenAI-compatible provider supplied tool choices or delayed responses. No external model or embedding endpoint was called. Java received responses from the actual Python Agent; its worker result was not replaced with a fabricated fixture.

**Result: 2/2 cases and 35/35 assertions passed.** Both tasks ended as `FAILED` with `error_code=agent_timeout`. They remain in the isolated database `lablens_v013_fault_d5997043`; no parent QA or main workspace database was used.

| Case | Controlled behavior | Persisted result |
| --- | --- | --- |
| Decision timeout | The first generation response waited 3 seconds; the Agent had a 1-second total budget. | 1 attempted model call, 0 tool calls, unknown generation tokens, explicit timeout trace; no automatic retry. |
| Embedding tool timeout | The local provider immediately selected `search_workspace(strategy=dense)`. The real Agent called the real local RAG HTTP endpoint, whose embedding provider response waited 3 seconds. | 1 known model call and synthetic token counters 8 prompt / 4 completion / 12 total preserved; embedding requests, inputs, cache hits and tokens `null`; model decision, tool start and terminal timeout trace persisted. |

The generation usage in the second case is a controlled transport value, **not actual model billing or inference**. The local provider observed exactly two generation HTTP requests and one embedding HTTP request across both cases. Late responses did not produce a success or another request.

Each case checked the terminal task through HTTP, waited for the late provider response, fetched the same task again, replayed the original idempotency key and read the JSON fields directly from MySQL. The refreshed response was identical; `execution_usage` and `error_result` matched the database. Stored traces retain the snapshot ID and document SHA256.

Evidence:

- `summary.json`: configuration, source/JAR hashes, every assertion, final/refreshed/replayed task and direct MySQL JSON rows.
- `http-observations.json`: raw JVM API requests and responses, including intermediate queued/running states.
- `provider-events.json`: local provider request payloads and late-response outcomes; authorization headers are never recorded.
- `cleanup-verification.json`: all owned ports closed, both failed rows retained, source and JAR hashes unchanged.
- `scripts/evaluate_workspace_agent_fault.py`: reproducible harness; rejects occupied/main QA ports and never overwrites an existing report directory.

The isolated ports were JVM **8084**, Python **8768**, provider **8770** and Redis **6392**. All owned processes stopped. The database was retained. The observed JAR SHA256 was `a2fe237c655108fb4391088807995546561b78bebffa4f16340e6844604f3ddf`.

For a new run, provide a fresh output directory and override local executable/credential/socket paths as needed:

```sh
.venv/bin/python scripts/evaluate_workspace_agent_fault.py \
  --credentials /path/to/ignored/services.env \
  --mysql-socket /path/to/isolated/mysql.sock \
  --output docs/evaluations/v0.13/agent-fault-new-run
```

These local controls verify timeout propagation, partial usage accounting and durable trace recovery. They do not measure semantic quality, independent evidence review, production availability or distributed recovery. A timeout also cannot cancel a request already received by a provider.
