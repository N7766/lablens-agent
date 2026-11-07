# Round18 recovery cross-review

Review type: AI sub-agent source review by the Python recovery implementer of the separately implemented JVM and browser recovery code. This is not an independent human evaluation, model-quality evaluation, or a claim that runtime tests passed. Runtime evidence is recorded separately by the integration owner.

## Reviewed scope

- `RecoveryStore` and `RecoveryController`: authenticated callback, owner/lease fencing, immutable state digest, ordered checkpoints, unknown-outcome admission, explicit resume and one-child transaction.
- `WorkspaceStore.submitTx`: conversation admission while an original turn or its recovery child is active.
- `recovery.js`: original-snapshot approval, role-dependent controls, duplicate click prevention and lost-response reconciliation.
- `RecoveryIntegrationTest`: the corresponding failure, race, role and admission cases in source. This reviewer did not execute the Java suite.

## Finding and disposition

The initial recovery child had no conversation identifier, so the normal conversation busy query would not detect an active continuation. A new conversation turn could therefore be admitted before recovery finished. The implementation now joins a child to its parent when counting active conversation tasks. The Java test covers this guard. The shared workspace/admission lock orders the two admission paths; the original conversation revision is not advanced twice.

The recovery report remains a separate task-history branch. The original failed conversation turn is immutable, and later conversation turns do not automatically inherit the child report. This is an explicit current limitation, not completed conversation-branch merging. The project documentation must retain this distinction.

## Reviewed invariants

- Only an active `RUNNING` task owner with an unexpired lease can persist a callback. Task identity, owner and service authentication are separate checks.
- An initial boundary must exist before any operation begins. Multiple operations in the same batch remain pending until a later complete boundary; a pending latest record never falls back to an older safe record.
- The JVM hashes the exact UTF-8 `payload_json` string. This avoids reserialization drift in floating retrieval scores and telemetry; the Python restore path separately verifies shape, exact source provenance, configuration and complete tool pairs.
- A resume transaction retains the original snapshot and creates at most one child. Existing child discovery and idempotency handling reconcile a lost response without creating another task/outbox entry.
- Browser GETs do not expose the opaque checkpoint payload. A viewer cannot trigger resume. The user must check approval for the original frozen snapshot before POST; network failure triggers a GET reconciliation, not an automatic repeated POST.
- Success, a continuation of a continuation, a later conversation revision, no checkpoint, and an unknown operation result are not eligible for recovery.

## Remaining verification boundary

These source observations do not replace the isolated process-kill/restart run, MySQL transaction tests, browser interaction checks, or final exact-source test suite. A completed transport result with unknown billing remains distinct from an unknown operation outcome; billing subtraction and aggregate reporting require their own JVM verification.
