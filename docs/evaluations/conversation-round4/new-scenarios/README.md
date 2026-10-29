# Round 4: isolated new synthetic conversations

The strict frozen criterion is **5/6 rounds**, with **6/6 delivered responses**, **0 blocked rounds**, and **17/42 permitted model calls**. All 6 tasks finished `SUCCEEDED`; J3 intentionally returned a source-backed missing-measurement response (`refused`) and Q3 intentionally requested clarification (`clarification_required`). These are successful interactions under their respective gold expectations.

This is **AI-authored synthetic evaluation with AI scoring**, not independent human evaluation. A separate isolated agent authored the seven short English source documents, six Chinese questions, gold, rubric, and runner before model execution. That agent read only the public workspace API README for protocol information, did not inspect production implementation or old evaluation material, and scored the sealed raw results itself. No external scoring-provider calls were made. Production-file contents were hashed as bytes for freeze verification, not inspected.

## Frozen execution and evidence

- Production commit: `b737ef7554ed368616f8485b55fed8666f574017`.
- Candidate-freeze SHA-256: `e94e94a02bb3636bc8fb5b9c0c35493dadd91fb55262fcb62041fb9cdbb8b6f9`.
- Authoring-manifest SHA-256: `a74697c6b912e2de9dcb836ba604241d8fb5a7d7cdeaba45db55a33ff88b1f4d`.
- Raw-manifest SHA-256: `2b47a820feb2057cd88d9e74ed75e7e87e790c67886f5b4955648150bb3c70e1`.
- All 59 source files matched the candidate freeze before and after the run. Each response reports the same workspace-agent implementation hash and a 7-call, 75-second Agent budget.
- Execution used only the local JVM API at `http://127.0.0.1:8083`, `agent` mode, BM25, top_k 5, one submission per round, and no retries. Client observation allowed 200 seconds per task; no observation timeout or cancellation occurred.
- Raw HTTP requests/responses, final tasks, conversation previews, usage and outcomes were sealed before semantic reading. Frozen inputs and gold were not revised or selectively rerun.

See [frozen inputs](fixtures.json), [gold](gold.json), [predeclared scoring protocol](scoring.md), [authoring manifest](manifest.sha256), [raw manifest](raw/manifest.sha256), and [per-dimension evidence and judgments](scores.json). Source verification is recorded in [preflight](preflight-verification.json) and [postrun](postrun-verification.json).

## Per-round result

| Round | Required behavior | Delivery | Strict result | Model calls | Client elapsed (s) |
| --- | --- | --- | --- | ---: | ---: |
| J1 | Apply v2.4 stable thresholds to P21 | Pass | Pass | 3  34.177 |
| J2 | Apply corrected P22 preview scope and reject combined acceptance | Pass | Pass | 3  58.310 |
| J3 | Keep corrected P22 referent and acknowledge absent peak RSS | Pass | Pass | 3  20.118 |
| Q1 | Apply r8 EU main thresholds to L31 | Pass | Pass | 4  54.322 |
| Q2 | Evaluate corrected L32 main and L33 mirror, including both overall outcomes | Pass | **Fail: incomplete combined decisions** | 3  36.189 |
| Q3 | Clarify whether “it” means L32 main or L33 mirror | Pass | Pass | 1  6.044 |

Client elapsed time runs from submission through the persisted terminal conversation response; it includes local API overhead and is not solely provider execution time. Exact values are retained in `scores.json` and `raw/outcomes.json`.

Q2 states all four comparisons correctly: L32 recovery fails at 26 > 25 minutes while its checksum passes at 99.97% ≥ 99.95%; L33 passes at 33 ≤ 35 minutes and 99.92% ≥ 99.90%. However, the saved final answer and its four assessments stop there and omit the requested combined decisions **L32 overall fails** and **L33 overall passes**. The frozen gold and rubric require combined decisions. Therefore strict scoring records one conclusion-completeness failure. The results are readily inferable, but the evaluator cannot supply omitted final decisions on the model's behalf. This is not counted as an incorrect number, wrong threshold, unsupported citation, or fabricated factual claim. See [the complete saved Q2 task](raw/Q2-final-observed-task.json), fields `result.answer` and `result.assessments`.

J3 correctly refuses to invent a measured peak RSS and distinguishes installed RAM from usage. Q3 asks which of the two corrected targets is intended, without silently assigning a retry interval. Neither is an incorrect refusal. Every final citation is an exact slice of a frozen synthetic source; semantic inspection separately confirms that those passages support the claims actually made. This citation result does not cure the Q2 omission.

## Separate dimensions, fixed denominator

| Dimension | Satisfied / all 6 | Observed failures | Unobserved / blocked |
| --- | ---: | ---: | ---: |
| Delivery | 6/6 | 0 | 0 |
| Required-evidence retrieval | 6/6 | 0 | 0 |
| Complete final conclusion | 5/6 | 1 | 0 |
| Citation entailment | 6/6 | 0 | 0 |
| Avoid incorrect refusal | 6/6 | 0 | 0 |
| Reference/correction handling | 6/6 | 0 | 0 |
| Version/profile/scope adherence | 6/6 | 0 | 0 |
| Strict end to end | **5/6** | **1** | **0** |

The retrieval row includes five rounds with observed required passages and Q3, where pure clarification requires no retrieval. The citation row includes five source-claim rounds and Q3 with no factual claim. These are explicit rubric satisfactions, not six demonstrated retrievals or six evidence-bearing answers. Observable-only denominators happen to be six in this run; no failed or blocked rounds were removed. No numeric or threshold claim was observed to be false in the delivered answers.

Usage: 17 known model calls, 0 embedding requests, 0 tasks with unknown provider usage; 53,378 prompt tokens + 3,854 completion tokens = 57,232 reported generation tokens. Calls by round are shown above. The configured 75-second Agent budget was preserved; this run makes no hard provider-cancellation guarantee.

## Limits

There are only two related conversations and seven short synthetic sources. Each workspace contains three or four documents, while top_k is five, so this is an intentionally small scope/citation/conversation diagnostic and does not establish large-corpus retrieval performance. Authoring and scoring were done by the same isolated AI agent. These results are descriptive, not independent external validation, a population-accuracy estimate, or evidence of production readiness. Browser UI behavior, real-user documents, cross-session memory, provider-failure recovery and broader adversarial robustness were not evaluated here.
