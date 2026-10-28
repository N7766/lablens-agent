# Round 2 cancellation fault proof

The final-source run passed **4 controlled cases / 35 checks**, with zero external model or embedding requests. It used real JVM, MySQL, Redis and Python services plus a scripted loopback provider. Provider decisions and token counts are synthetic transport fixtures, not semantic-quality measurements.

| Case | Checks | Observed behavior |
| --- | --- | --- |
| Queued cancellation | 7 | Durable `CANCELLED`, no worker start, zero usage, repeat cancellation and original-key replay preserve the same task. |
| Running cancellation | 11 | `CANCEL_REQUESTED` stays active while the provider call is blocked; the next conversation turn receives 409; release ends in `CANCELLED`, retains one call / 12 synthetic tokens, starts no tool or follow-up provider call, and publishes no late report. |
| Nested retrieval cancellation | 9 | The loopback retrieval child shares the stop flag; cancellation after the first embedding request prevents a second embedding or decision call and retains known usage. |
| JVM restart and unreachable worker | 8 | Cancellation remains durable and blocks the conversation while Python is unreachable; restarting the worker acknowledges local stop without replaying the task or inventing lost-process usage. |

`summary.json` contains assertions, source fingerprints and the SHA-256 of the immutable JAR copy used for both JVM starts. `http-observations.json` preserves requests and task/history responses. `provider-events.json` preserves the controlled provider traffic. `source_unchanged` and process-cleanup flags are recorded in the summary.

Reproduce from the repository root after building the workspace JAR:

```sh
.venv/bin/python scripts/evaluate_cancellation_round2.py \
  --output docs/evaluations/conversation-round2/cancellation-reproduction
```

Use a fresh output directory; the runner refuses to overwrite existing evidence. It requires local MySQL/Redis tooling and local database credentials, creates an isolated database, owns ports 8084/8768/8770/6392, and stops every process it starts. Existing main and QA services are left alone.

The worker registry is one-process local state. `CANCELLED` confirms local execution has exited; it does not forcibly stop an upstream provider request. An unreachable worker deliberately leaves cancellation pending. No checkpoint resume, distributed cancellation, production capacity or independent semantic evaluation is established by these controls.
