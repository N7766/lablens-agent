# Round 5 new-scenario frozen protocol

This is a tiny AI-synthetic diagnostic holdout: 2 independently invented scenarios, 4 documents per scenario, and 2 conversational turns per scenario (4 planned turns). English source documents are queried in Chinese. The second turn depends on the first turn's saved response. It is not a representative benchmark, an estimate of general model accuracy, a human-authored test, or independently human-scored evidence.

## Isolation and authorship

The author read only `services/workspace/README.md` as the API protocol. The author did not inspect production code, prior evaluation questions/results, `.env`, private history, other agents' task contents, or consult an external model to write the questions. This evaluator is the same AI author who prepared the gold. Keep this limitation prominent in the published report. All names, run IDs, dates and records are synthetic.

Only the files inside this `frozen` directory are pre-run inputs. SHA-256 for each file is recorded in the sibling `freeze-manifest.json`, whose own digest is the freeze identity. Gold and rubric must not be revised using observed results. A protocol defect discovered later must be disclosed, and any replacement must be a separately versioned study rather than overwriting this one.

## Authorization and budget

Do not submit tasks before the root evaluator gives explicit `GO`, the isolated QA base URL, and the frozen candidate Git SHA. The runner requires those values plus an operator attestation that the candidate has at most 6 decision-model calls and 1 review-model call per task, no model retries, and a 75-second service boundary budget. Those controls are asserted by the operator and should also be checked against captured status when status exposes them; the public API has no per-task fields for setting these limits.

Run `mode=agent`, `strategy=bm25`, `top_k=5`. Use one conversation per scenario. Use saved current document revision consent and current conversation revision on each turn. Use one unique idempotency key per planned turn. There are at most 4 model tasks, at most 7 provider calls per task, and at most 28 provider calls overall. BM25 is selected to avoid provider embedding calls. Retrieval tools may run but must not add external model calls. No uncertain task POST is retried, no task is resubmitted, no model is retried, and no failed turn is replaced.

The client observes each submitted task for up to 200 seconds using read-only GET polling. This is an observation window, not a model budget. On an observation timeout, it sends at most one cancellation control request and stops further submissions. Unknown POST outcomes, exceeded/unknown reported usage, unknown safety configuration, or infrastructure errors stop new task submissions; they do not justify retries. Persist uncertainty rather than writing zero usage. Dependency blocking keeps all 4 planned turns in the primary denominator.

Public usage schema supplied by the root evaluator: `task.execution_usage.model_calls` counts attempted model provider calls, including uncertain outcomes. Embedding counts are `embedding_requests`, `embedded_inputs`, `embedding_cache_hits`; token counters may be null. `provider_usage_unknown`, `budget_exhausted`, `cache_reused` are booleans; the outer `task.usage_unknown` is retained separately. Fallback usage may appear in `result.usage` or `error_result.usage`. Missing counters remain unknown. Request lifecycle trace events may independently show `request_started` / `request_finished` by `request_id` and `phase` (`decision` / `review`); absent trace events are not zero calls.

## Dependency gate

A dependent turn can run only when its predecessor is `SUCCEEDED` and the corresponding persisted conversation turn has a nonblank `answer`. A saved substantive clarification counts as a response even if semantically wrong; it must be scored accordingly. A failed, cancelled, still-running, response-less, or uncertain predecessor makes its dependent turn `blocked_no_valid_prior_response`. Do not fabricate a previous answer or run the dependent question independently. A semantically wrong but structurally valid first response does not block the second turn.

## Retained evidence and sealing

Save exact request bodies, raw response bytes, HTTP status, safe headers, wall times, transport errors, task snapshots, conversation snapshots, configuration/status snapshots, task usage, and operator/candidate metadata. Save every polling observation. Request bodies contain only synthetic materials; never copy credentials or environment files into artifacts. The runner uses a proxy-free urllib opener and refuses redirects and non-loopback QA URLs.

The runner first finishes all permitted collection and then writes a `raw-seal.json` with SHA-256 of every raw/metadata artifact. No semantic response content is printed while running. Only after verifying that seal may the evaluator read answers and traces and create semantic annotations. An incomplete unsealed run must be completed or explicitly sealed as interrupted before semantic reading. Do not repair/re-run a task after observing its answer. `annotations.json`, scored output, and a report live outside the raw sealed directory and must refer to the frozen input and raw-seal identities.

## Semantic scoring

Score six distinct dimensions for each completed response, using source text, the frozen gold, final report text, actual cited passage text and raw retrieval trace. This is semantic review, not substring checks. A correct quotation can still fail to entail the claim it is attached to. A retrieved source can remain unused, misapplied to another version, or contradicted by stronger evidence. Current configuration does not establish historical execution.

Each dimension receives 0, 1, or 2. Use null only when the necessary trace/evidence is unavailable, with an explicit reason. Never infer full retrieval coverage from the presence of correct final prose.

| Dimension | 2 | 1 | 0 |
| --- | --- | --- | --- |
| Retrieval coverage | Trace contains passages supporting every required source facet for this turn, including applicable contrary/limiting evidence. | Some relevant evidence retrieved but one required facet is missing or ambiguous. | No required evidence, or only wrong-scope evidence retrieved. |
| Conclusion correctness | Every asserted substantive conclusion is consistent with the supplied evidence and the frozen gold; uncertainty is calibrated. | Main answer is correct but has a material ambiguity or minor unsupported assertion. | A core conclusion is wrong, a forbidden causal/historical inference is made, or substantive answer is absent. |
| Completeness | All frozen required conclusions and user-requested aspects are addressed. | Main conclusion is present but at least one required aspect is omitted. | Main requested conclusion or most requested aspects are omitted. |
| Citation entailment | Every externally verifiable material claim has a correctly scoped citation whose quoted passage(s) jointly entail it; explicit limitations support insufficiency. | Citations exist, but a material claim lacks support or a passage only partly supports it. | Wrong source/version, a quote contradicts the attached claim, invented evidence, or no usable source support. |
| Erroneous refusal | No erroneous refusal; all answerable parts answered, and genuine gaps appropriately bounded. | A partially answerable part is needlessly deferred/refused, but useful requested content remains. | Blanket refusal/clarification substitutes for an answer the evidence permits, or answer contains no useful bounded conclusion. |
| Reference and scope | Correctly resolves cross-turn reference, corrections, target version/run, user-attribution boundaries, and required comparison. | Target is mostly right but one reference/comparison/attribution boundary is unclear. | Wrong entity/version/run, ignores correction, imports stale prior scope, or treats user input as source-verified. |

For retrieval use `required_retrieval_sources` as source-facet guidance, not a requirement for a particular literal query. An equivalent passage containing the same required facts may satisfy the facet; explain it. The current gold lists source IDs so all assignments can be audited. Citation entailment is judged per claim, including conjunctions and causal relations, not merely per source ID. The evaluator records claim IDs, evidence pointers, observed claim/paraphrase, rationale, and any counterevidence.

A strict turn pass requires all six dimensions to be 2. An all-dimension average is descriptive only and cannot hide a wrong conclusion. Record both strict passes / 4 planned turns and strict passes / completed evaluable turns. Failed and blocked turns remain in the primary four-turn denominator and have null semantic dimensions, not invented semantic scores. Report completion, failure, blocking and unknown usage separately. Also report each dimension's full-score count / 4 and count / semantically evaluated turns so a survivor-only success cannot hide infrastructure failure.

The gold allows natural paraphrases, concise answers, jointly supporting multiple citations, and explicit correction of a mistaken predecessor. It does not require a fixed wording or a preselected answer format. Do not score mere matching words, tool completion, `supported`, exact quotation existence, or a same-model review pass as independent proof of semantic correctness.

## Publication

After execution and scoring only, publish the frozen inputs, freeze manifest, raw seal, safe raw HTTP/task/conversation/usage evidence, semantic annotations, score output and concise report under `docs/evaluations/conversation-round5/new-scenarios`. Retain failed/blocked cases and limitations. Do not publish only successful examples, do not claim independent human evaluation, and do not alter production code or Git commit from this evaluator role.
