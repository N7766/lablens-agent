# Exposed-scenario development regression

**This is development regression on already exposed synthetic questions, not a fresh blind evaluation, human independent review, or production validation.** The original independent package remains unchanged: 0/2 submitted tasks succeeded and four dependent turns were blocked. This run reuses the exact original two workspaces, six questions and frozen rubric after protocol hardening. No question, evidence document or gold criterion was changed.

## Outcome

The complete multi-turn regression **did not pass**. Four turns were submitted once each: two first-turn answers succeeded, the explicit-correction follow-up timed out, and the ambiguous follow-up failed protocol validation. Two dependent final turns were blocked without submission. No retries or model repair calls were added.

| Turn | Runtime result | Worker time | Model calls | Semantic / rubric finding |
|---|---|---:|---:|---|
| cb1 | SUCCEEDED / answered | 61.448 s | 4 | All numeric claims and gate comparisons supported; synthetic/local qualification omitted from answer prose |
| cb2 | FAILED / agent_timeout | 75.658 s | 3 | Corrected state and one prior turn delivered; no final answer |
| cb3 | BLOCKED | N/A | 0 | Failed cb2 dependency |
| hb1 | SUCCEEDED / answered | 58.305 s | 4 | Both candidates' metrics and rejection of recall-as-answer-quality supported; synthetic/local qualification omitted from prose |
| hb2 | FAILED / agent_protocol_error | 54.892 s | 3 | Expected clarification was not delivered |
| hb3 | BLOCKED | N/A | 0 | Failed hb2 dependency |

Operational task success is **2/4 attempted**, with 2/6 planned turns blocked. Neither three-turn scenario completed (0/2). Core factual semantics pass on **2/2 delivered answers**, with no unsupported final assertions found in the statement audit. Strict all-criteria pass is **0/4 attempted / 0/6 planned**: the two answers omit the frozen rubric's synthetic/local scope qualification in user-facing prose, and both attempted follow-ups fail operationally. Scope omission is not classified as invented numeric facts: source metadata still says synthetic, cb1 retains its same-condition 120-request sample, hb1 retains 50-question denominators, and neither makes a production claim. This distinction is preserved in the JSON grades.

## Per-axis evidence

| Axis | Result | Scope |
|---|---|---|
| Required document coverage | 4/4 required document-turn pairs in executed turns | Four additional planned pairs were blocked; the ambiguous turn had no required document |
| Source integrity | 22/22 retrieved hit occurrences valid; 17 unique turn/chunk pairs | Exact fixture substring and SHA256 checks, with current-workspace membership |
| Initial referents | 2/2 delivered answers correct | Does not prove later pronoun resolution |
| Scope constraints | 0/2 delivered answers fully preserve synthetic/local scope in prose | Numeric and denominator correctness reported separately |
| Core factual answer semantics | 2/2 delivered answers supported | The hb1 proposition “retrieval metrics prove answer quality” is explicitly labelled contradicted and rejected, not counted as an asserted false fact |
| False refusal | 0/2 delivered answerable responses | One answerable turn has no output due to timeout; it is not silently counted as a correct response |
| Expected clarification | 0/1 attempted ambiguous turn delivered a clarification | Protocol failure, not a successful clarification or a false-refusal answer |
| Pinned state transport | 4/4 executed turns match intended state | cb2 includes the explicit goal, constraints and focus correction |
| Prior terminal context | 2/2 attempted follow-ups retain sequence 1 | Both follow-up outputs fail; this proves context transport, not behavioral memory correctness |
| Successful behavioral follow-ups | 0/2 attempted; two more blocked | Missing/contradictory final conclusions and three-turn carryover remain unvalidated |

All statements and explanations in both final answers, including repeated numeric assertions and verdict-labelled propositions, were inspected against the full frozen ground truth and current retrieved source excerpts. `grading/statement-audit.json` records the entailment analysis; `grading/grades.json` preserves every original criterion and each turn's status. The scope failure applies the original rubric's “Synthetic/local limits must remain clear” and pinned constraints. It does not retroactively add a new metric-accuracy requirement.

## Failure traces

**cb2 timeout:** decision 1 selected three searches; decision 2 verified the retrieved sources; decision 3 reached the shared 75-second deadline. The result records `budget_exhausted=true`, `provider_usage_unknown=true` and null aggregate generation tokens. The corrected pinned state and prior sequence 1 survived every recorded context-budget pass. No final answer is credited from intermediate search results.

**hb2 protocol rejection:** search and source verification completed, then decision 3 recorded:

```json
{"has_content":false,"finish_reason":"stop","has_tool_calls":false,"tool_call_count":0}
```

No `request_clarification` result or final answer was saved. This demonstrates that the hardened request path still received a response without usable tool calls in this instance; it does not establish provider-wide enforcement behavior. No retries followed either failure.

## Usage and timing

The run attempted **14 model calls**, below its 42-call cap. Embedding requests were zero. Three tasks have complete token usage totalling **55,029 tokens** (52,601 prompt + 2,428 completion). The timed-out task's two completed decision records add **13,500 known tokens** (13,198 prompt + 302 completion). Therefore **68,529 tokens is a confirmed lower bound**, not the full run total: usage for the timed-out third call remains unknown. The full aggregate token total is null in the machine-readable grades, never zero.

The four worker durations were 61.448, 75.658, 58.305 and 54.892 seconds (mean 62.576 seconds). The two successful-answer durations average 59.877 seconds. This tiny local sample does not support p95 latency, throughput, SLA, cost, or production claims. Successful reports identify their same-model review as `Qwen/Qwen3-235B-A22B-Instruct-2507`; that review is not independent semantic proof.

## Freezes and reproducibility

- Original input/rubric SHA256: `6305e6e4f60f2e3a855bdd0470a6fc3f48c9800fe6c3d053f7226fd8c1dfd2a1`
- Development raw-output SHA256: `0bf7bb454f50faf5844ad7ace411209ee6d35f5a0867bb04ebd955715fb07311`
- Pre-run Python file fingerprint manifest SHA256: `b29891f1195487e271c82b9e1ba087ef75584ece371388fc5ab79b335938e11f`
- Agent implementation SHA256 from all executed tasks: `b8fd6846d08ed3ad73b690f53fa69c21ff715cb19c9186f0c510a0d5601e40d5`
- Context implementation SHA256 from all executed tasks: `2390ef39bfdfe89971a6f7f63ea8e8e19263cbf9629a1bcc8f395cbc2281e209`

All 429 tracked Python file hashes match before and after inference. Every one of the original independent package's 106 sealed files still matches its manifest. The runner uses public JVM API calls on isolated port 8083, explicit approved document revisions, expected conversation revisions and unique idempotency keys, with loopback proxy inheritance disabled. Every raw request/response, complete task, usage record, pre/post conversation and source fingerprint is retained under `run/`. The final package manifest seals this development result separately from the original independent evidence. The runner's `--evaluation-label development-regression` keeps the run identity explicit.
