# Python recovery correctness review

This is an AI cross-review of the Round 18 working tree, not independent human
review or a model answer-quality evaluation. The reviewer implemented the
separate conversation-summary slice and did not implement the recovery journal,
restore path or fault harness. Review scope: `lablens/checkpoints.py`, the recovery
paths in `lablens/workspace_agent.py` and `lablens/rag_api.py`,
`tests/test_workspace_recovery.py`, `scripts/round18_recovery_fixture.py` and
`scripts/verify_workspace_recovery.py`. The JVM recovery admission and result
accounting code were read where needed to follow the full path.

## Findings and disposition

1. **Resolved — checkpoint acknowledgment could consume the call allocation.**
   The decision loop originally checked its remaining time before writing the
   durable operation receipt. That callback can consume up to three seconds.
   After acknowledgment, `max(.001, deadline - now - review_reserve)` could still
   start a provider request after the decision allocation was exhausted.
   `mark_operation` now checks cancellation, the absolute active deadline and
   the structured decision's final-review reservation after acknowledgment and
   before incrementing provider usage or starting a request. The reviewer reran
   both deterministic delayed-acknowledgment regressions: **2 passed**.

2. **Resolved — distinguish an unrestored attempt's zero usage from cumulative
   restored usage.** A resumed child can fail memory expiry or configuration
   validation before the checkpoint is restored. Its zero new calls are valid
   per-attempt accounting, but must not imply that the original execution was
   free. The worker now labels these results
   `current_execution_only_before_checkpoint_restore`; validated restored
   results use `cumulative_original_and_resumed_execution`. Unvalidated saved
   counters are not copied into an early failure. The JVM derives a per-attempt
   delta only when recovery provenance binds the restored counters to the
   immutable checkpoint. Tests cover expired memory, mismatched configuration
   and unavailable/invalid-memory helpers.

No unresolved replay or partial-batch correctness finding remained in the
reviewed path. This is a scoped review, not proof that every possible execution
or provider behavior is safe.

## Checked invariants

- A durable operation-start receipt precedes provider work. A failed callback
  stops execution without retrying that callback or starting another provider
  request. A complete boundary is written only after an entire tool batch.
- Restore validates the serialized state hash, request/configuration hashes,
  source identity, complete tool exchanges, call identifiers, counters and
  budget. Execution policy hashing includes the new summary implementation.
- The next step, observed/verified sources, exchanges and usage are restored;
  completed retrieval is not called again by the restore path. Active elapsed
  time is cumulative, downtime is explicitly excluded, and a lost boundary
  acknowledgment is charged its full callback reservation.
- A pending operation remains the latest durable state. The JVM never silently
  falls back to an earlier completed boundary. Partial batches and interrupted
  review therefore block continuation as `unknown_outcome`.
- The fault harness kills only the worker process it created. The boundary gate
  is entered after the JVM has durably accepted the checkpoint. Fixed local
  model responses and synthetic usage are labeled; no external provider is
  needed by this harness.

The reviewer additionally ran the full recovery-focused test file after the
fixes: **34 passed**, with the existing Starlette/HTTPX deprecation warning.
The primary run's full-suite logs and frozen real-HTTP fault-matrix results are
separate acceptance evidence; this review does not claim to have executed that
matrix itself.

## Deliberate limits

- `unknown_outcome` supports inspection and blocks replay. It does not query a
  provider's billing/result system, offer a manual override or prove that a
  disconnected provider stopped computation.
- Recovery permits one explicitly approved continuation. It is not an automatic
  retry mechanism or an indefinitely resumable workflow engine.
- The matrix's provider-request count independently measures local model-shaped
  requests. The non-replay retrieval assertion uses the persisted tool trace
  together with the reviewed restore path, not an independent packet counter.
- Fixed scripted responses test protocol, durability and accounting; they do not
  establish semantic answer quality or production traffic capacity.
