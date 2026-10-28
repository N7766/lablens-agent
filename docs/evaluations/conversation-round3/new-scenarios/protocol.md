# Round 3 isolated synthetic local holdout

This evaluation is freshly AI-authored and AI-graded. It is not a human-independent study, public-source evaluation, external-user trial, or production benchmark. The author was allowed only the prior runner plumbing, and the parent provided conversation state field constraints. No implementation, prior questions, prior gold, or prior outcomes were inspected.

There are two scenarios, three dependent Chinese turns per scenario, and four short English source documents per scenario. Corrections are supplied in user text and through the documented explicit state update path. Ambiguity turns clear focus explicitly. This does not measure automatic state extraction from arbitrary dialogue.

The input documents, questions, gold rubric, and runner are frozen before any inference. Only counts and hashes are disclosed to the implementation agent until code is frozen, one-shot inference is complete, and grading is frozen. Six task submissions and forty-two model calls are hard ceilings. No automatic retry, provider retry, question rewrite, or rerun is allowed. Public API polling is not an inference retry. Failed prerequisites block dependent tasks; all blocked and failed tasks remain in denominators.

Every first raw HTTP response, submission, terminal task, pre/post conversation, per-turn system status, registered document state, source fingerprint map, usage fields, and timing is preserved. The grader scores final semantic completeness, citation entailment, retrieval coverage, false refusal, referent resolution, and source scope separately. Retrieval or a real citation cannot compensate for a false final claim. Pure clarification may have no citations or retrieval.

The source labels use synthetic:<scenario>/<file>; source bytes are hashed. Runtime code identity comes from parent-frozen approval plus public system snapshots and returned task fingerprints, not from reading implementation. The code-identity evidence limitations must be disclosed if the public response lacks a fingerprint.
