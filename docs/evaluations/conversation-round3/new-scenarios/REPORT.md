# Round 3 fresh synthetic local evaluation

## Outcome

The frozen candidate did not deliver any completed final report in this run: **0/6 planned turns**, or **0/2 submitted turns**. Both first-turn tasks terminated with `FAILED / agent_timeout`; their four dependent turns were never submitted under the predeclared no-retry protocol. This is two timeout failures and four dependency blocks, not six observed factually wrong answers.

Both first turns retrieved all required evidence units (**2/2 attempted first-turn retrieval coverage**). Neither produced a final report, so retrieval coverage cannot be reported as answer correctness or citation entailment. The user-correction, missing-versus-contradicted follow-ups, and ambiguous-reference turns were blocked and remain unmeasured.

This is a fresh **AI-authored, AI-graded synthetic local holdout**, authored in isolation from implementation and older questions, gold, and results. It is not human-independent, public-source, external-user, or production evidence. The candidate was frozen before inference. No rerun or post-run input/gold edit occurred.

## Scope and immutable evidence

Two scenarios each contain three dependent Chinese questions and four invented English source documents. Cedar compares controlled cold-room trials; Harbor compares archive-restore trials. The evidence deliberately separates current approved records from superseded or different-workload records. Explicit goal/constraints/focus state updates accompany user corrections, so this evaluates the documented state-update path, not automatic state extraction.

- Candidate commit: `fed3bd6d726f3eb01e3bf6ffb06c4ce42f81c278`.
- Candidate freeze manifest SHA-256: `b049ed161f3ffa4ea96fcfc4f488fc3b362eaac89f4bb247a8298a4cd01a2393`.
- Input manifest SHA-256: `1ac9910b15ddf5efb704b03120d955d3ae54afd9422ad5f9e69684d76107ba0b`.
- Frozen runner SHA-256: `2bc97ddc8aacaaac0d61a49d44c123ac45d3f534adc0637342359317eab3d82e`.
- Frozen gold SHA-256: `a0949662947407a355d0e62513085e29e497053e147e88b644eb9cb5db5f9fe7`.
- Raw output manifest SHA-256: `6524484ae742ed115f90074a046bf0a5d46e1c990c9032c1cb523789e3bf7a7f`.

The per-task returned assessment, agent, and quality implementation hashes match the approved identity. Document revision 4 and all document bytes remained unchanged before/after each submitted turn. Exact hashes, document versions, source labels, timestamps, and context hashes are retained in `grading/runtime-audit.json` and in each task directory. Public system status does not independently expose a process-wide source fingerprint; observed task implementation hashes and the parent's verified frozen-code approval supply that evidence.

## Per-turn results

| Turn | Submission | Outcome | Reported model calls | Final report |
|---|---|---|---:|---|
| cedar-t1 | Submitted once | FAILED: agent_timeout | 4 | None |
| cedar-t2 | Not submitted | Dependency blocked | 0 | None |
| cedar-t3 | Not submitted | Dependency blocked | 0 | None |
| harbor-t1 | Submitted once | FAILED: agent_timeout | 3 | None |
| harbor-t2 | Not submitted | Dependency blocked | 0 | None |
| harbor-t3 | Not submitted | Dependency blocked | 0 | None |

All six turns remain in the complete-report denominator. The zero call counts on blocked turns mean no task submission; they do not replace unknown usage from the two submitted tasks.

## Separately graded axes

| Axis | Pass | Fail | Blocked / unavailable | Not applicable |
|---|---:|---:|---:|---:|
| Complete semantic report | 0 | 2 | 4 | 0 |
| Final citation entailment | 0 | 0 | 6 | 0 |
| Required retrieval coverage | 2 | 0 | 2 | 2 |
| False-refusal behavior | 0 | 0 | 6 | 0 |
| Delivered referent resolution | 0 | 2 | 4 | 0 |
| Delivered source-scope conclusion | 0 | 2 | 4 | 0 |

The frozen rubric counts a submitted task with no final answer as a delivery failure for semantic completeness, referents, and source scope. Those fail entries do **not** establish that an emitted final answer chose the wrong referent or scope; no final answer exists. Citation entailment and false-refusal behavior cannot be scored from an absent final answer. Pure clarification turns were designed with no required retrieval and are not applicable on that axis; they were still blocked as conversation turns.

Retrieval coverage was checked against the actual returned passage text, including thresholds, arithmetic premises, version/workload limits, and dashboard precision. A source title or citation ID alone did not count. `grading/grades.json` lists the exact required evidence units and passage IDs.

## Runtime and usage limits

The once-only run made **2 task POSTs**, reported **7 attempted model calls**, and stayed within the 6-task / 42-call ceiling. There were **5 model-decision responses with returned usage** and **2 final-review starts without a returned review result**. The service records both tasks as `provider_usage_unknown=true`. The API evidence does not establish whether those two review requests were received or billed by the provider, so neither completion nor zero cost is assumed.

Observed completed decision calls contain 19,777 prompt tokens and 1,336 completion tokens, totaling 21,113 tokens. This is a **partial observed token count**, not total/billable usage. Full generation-token fields remain null for both tasks. No task/provider retry occurred.

Both task traces end with `review_started` followed by `stopped: timeout`; neither has a review result. The agent reports a 75-second budget and a 7-call maximum. Worker durations were 75.583 and 75.598 seconds; runner turn durations were 76.540 and 76.493 seconds, including polling and API overhead. Total run wall time was 153.074 seconds. All 106 recorded HTTP exchanges returned without a recorded HTTP/transport error.

These observations establish that the agent time budget expired while final review had started. They do not identify whether provider latency, request size, scheduling, or another internal factor consumed the remaining budget, and they do not justify an inference about model capability. At the final status snapshot, RUNNING=0, QUEUED=0, and active workers=0. No direct provider-side cancellation or billing query was performed.

## Diagnostic drafts, excluded from final-answer scores

Cedar's intermediate draft computed the reductions and stated that only Reed passes. Its Dune overall-failure assessment attached only the rule passage to that specific claim, although earlier draft rows cited the measurements. Harbor's intermediate draft computed the fractions and strict latency boundary, but marked the true proposition that Quill has 150 seconds and fails the strict threshold as `contradicted`; it also omitted an explicit overall neither-passes sentence. These are retained diagnostic observations, not completed-report grades. The unfinished review might have changed them; no such result was observed.

## Artifact inventory and reproduction boundary

- `inputs/scenarios.json` and `inputs/sources/`: frozen questions, explicit state updates, and invented documents.
- `gold/rubric.json`: frozen semantic requirements, evidence requirements, prohibited conclusions, and six-axis rules.
- `runner.py`, `protocol.md`, `frozen-manifest.json`: pre-inference harness and protocol with immutable hashes.
- `run/`: complete first API submission/terminal responses, every HTTP exchange, conversations, snapshots, source maps, usage, runtimes, and approved code identity. The API exposes task/action traces, not raw provider HTTP response bodies.
- `raw-output-manifest.json`: raw-response hashes frozen before grading.
- `grading/grades.json` and `grading/runtime-audit.json`: final first-response grades and runtime accounting.
- `grading-manifest.json`: hashes freezing this report and the grading files.

The copied bundle includes the completed `run/` directory, and the frozen runner refuses to run if that directory exists. Do not delete it or rerun these exposed cases as fresh evidence. Any later development regression must be labelled as exposed-case regression; any new holdout requires new frozen cases.
