# Round 1: newly authored synthetic conversation evaluation

This is an **AI-authored, AI-graded local evaluation**, not a human independent assessment or production validation. The author did not read implementation code or existing case questions. The public JVM API README was read to construct the runner. Implementation files were only hashed. Two synthetic English-document workspaces and six Chinese user turns were authored and frozen with a separate rubric before any API request. Inputs and gold remained private until inference and grading were sealed.

## Result

**Not accepted.** Both scenario-opening turns failed with `agent_protocol_error`; neither produced a final answer. Their four dependent turns were marked **BLOCKED** and were not submitted. The runner made one attempt per submitted turn and no automatic retries. It did not invent successful prior answers to continue the conversations.

| Axis | Observed result | Interpretation |
|---|---|---|
| Task success | 0/2 attempted; 4/6 planned turns blocked | Planned suite achieved 0/6 passing turns; two conversations remain incomplete |
| Required document retrieval | 2/2 required documents in executed turns | Remaining 6 required document-turn pairs were untested; not answer accuracy |
| Source integrity | 9/9 returned chunks matched fixture text and SHA256 | Exact sources exist; this does not establish semantic entailment |
| Referent and constraints | 0 assessable final answers | No behavioral pass rate; the follow-up correction/referent cases were blocked |
| Semantic answer delivery | 0/2 attempted turns delivered a usable answer | No final factual assertions exist to grade; factual accuracy is N/A, not 0% or 100% |
| False refusal / clarification | 0 assessable final answers; 0/1 planned clarification turns executed | The intentionally ambiguous turn was blocked; protocol errors are not counted as false refusals |
| Initial pinned state | 2/2 matched the frozen user state | Confirms transport/preservation only |
| Persistent failed history | 2/2 available after conversation reload | Each conversation had revision 1 and its failed turn; no cross-turn memory success claim |
| Scoped factual memory | Current retrieval valid on 2/2 attempted turns; 0/4 follow-ups executed | History is labelled untrusted; behavioral use of prior turns remains unvalidated |

The complete frozen per-turn rubric and criterion statuses are in `ground-truth/rubric.json` and `grading/grades.json`. `grading/statement-audit.json` audits the absence of final assertions instead of treating retrieved quotations as a successful answer. `grading/source-checks.json` records exact source checks.

## Usage and latency

| Turn | Task outcome | Worker duration | Model calls | Prompt tokens | Completion tokens | Total tokens |
|---|---|---:|---:|---:|---:|---:|
| cb1 | FAILED / agent_protocol_error | 32.817 s | 3 | 14,992 | 464 | 15,456 |
| hb1 | FAILED / agent_protocol_error | 35.853 s | 3 | 14,091 | 544 | 14,635 |
| Total | 2 failed tasks | mean 34.335 s | 6 | 29,083 | 1,008 | 30,091 |

The bound was six Agent tasks and 42 model calls; actual use was two tasks and six model calls. Embedding requests were zero (BM25). Both usage records are known, with no cache reuse and no exhausted budget. These are two failed-task durations, not successful-answer latency, throughput, an SLA, or production performance. The run owner configured `Qwen/Qwen3-235B-A22B-Instruct-2507`; the public JVM task response does not independently echo that model identifier.

## Failure evidence and scope

Both traces completed `search_workspace` and `verify_sources`, then stopped with `protocol_error` on the third model decision. The public error is: `Agent tool protocol or evidence report was invalid; execution stopped.` Neither saved task exposes the exact invalid decision text, so this evaluator does not infer the precise protocol defect. The failure was not reported as timeout, context exhaustion, missing evidence, or embedding failure.

The saved context metadata shows 65,536 estimated units, preserved current question and pinned state, valid tool pairs, and no dropped source IDs. These are conservative byte estimates rather than exact model-token limits. Both first turns have zero historical turns; the previous-20-terminal-turns mechanism and long-context behavior were not exercised. Tests of explicit goal correction, pronoun carryover, ambiguity clarification, contradictory citation counts, and missing cost were planned but blocked by the failed first turns.

Any future use of these now-exposed questions for a fix is **development regression testing**, not a fresh blind evaluation. Preserve these original failures and author new private cases for any later independent round.

## Frozen artifacts and execution

- Input/rubric manifest SHA256: `6305e6e4f60f2e3a855bdd0470a6fc3f48c9800fe6c3d053f7226fd8c1dfd2a1`
- Raw-output manifest SHA256: `7337aeb119bcde87f96199cd8532c245402c271fa1c73fb59f5bbd00e05de535`
- Agent implementation fingerprint exposed by both tasks: `1c25c51a23ed930778bc7ae686d8d071dc6265be9558197cf50995b62ff83a40`
- Conversation implementation fingerprint exposed by both tasks: `bdf864a782bdc1108941a66c9c77d19db07f96a3f2c408dc552542e37523b48c`
- Python file fingerprint manifest SHA256: `b51acdb8714a72406ea4012e94e83bc1d855f1b53e238b10e758d84d7b69fa13`

The isolated JVM endpoint was `http://127.0.0.1:8083`, backed by the real worker on port 8767. HTTP proxy inheritance was disabled for loopback calls. Workspace revisions were approved explicitly and conversation revisions refreshed before each submission. Every raw HTTP request/response, submission, terminal task, persisted conversation, usage record, and source fingerprint is retained under `run/`. The final package manifest seals inputs, rubric, raw output, grading and this report. The runner is `scripts/evaluate_conversation_round1.py` and deliberately refuses an existing run directory; rerunning exposed inputs requires a separately named development run and is outside this sealed evidence.
