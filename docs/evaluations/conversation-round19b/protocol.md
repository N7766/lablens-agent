# Round19b: one decision per immutable assessment

Baseline: `be1cb55697b84750cbad82e40a3151f7cc84b809`, clean checkout observed before edits.

This follow-up preserves every sealed Round19 request, response, failure and score.
The actual v3 failure retained and replaced `a1` simultaneously. The candidate v4
uses a required object property per immutable ID, with one retain/replace action.
It does not repair, deduplicate, retry or reinterpret the old response.

## Predeclared local acceptance

- Keep the exact original failure regression and the v3 default path.
- Opt in to `scoped-review-decisions-v4` explicitly; reject unknown protocols.
- Reject missing/extra IDs, duplicate raw JSON keys (including escaped aliases),
  non-finite JSON values, illegal retention, invalid evidence and extra fields.
- Restore immutable statement order without modifying the draft or inputs.
- Apply existing evidence, history and causal checks even when every row is retained.
- Preserve raw response/usage and terminate after one invalid review, without fallback.
- Freeze the candidate protocol, payload, implementation and deployment identity
  together; the run mode cannot override the frozen protocol.
- A separate local outbound gateway must durably reserve a slot before each send,
  bound concurrent sends and consume failed/uncertain attempts without retry.

## Evidence boundaries

Local fixtures and HTTP simulations are engineering checks, not real model quality
or vLLM grammar acceptance. The previous three-call GPU allowance is exhausted and
that instance was destroyed. New real-model calls require a separate bounded plan
and approval. Candidate live acceptance is pending; no new paid request has been sent.
