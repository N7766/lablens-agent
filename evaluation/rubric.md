# Frozen evaluation rubric v0.2

Freeze this file, cases, source and prompts in each manifest BEFORE live requests. No edits to model instructions or cases based on holdout answers. Development: original eight intents. Holdout: new ordered run pairs, rank/selection and multi-constraint queries, non-existent metadata and action requests. Related concepts still overlap; this is a narrow within-project holdout, not an independent dataset. Paraphrases within a family stay in one split.

Each answer is reviewed against actual runs/notes and case-specific rubric:

- Factuality: all factual claims correct, numerics use correct units and ordering, no unsupported performance claims. Missing an answer is not automatically factuality failure if no false facts.
- Citation support: every substantive claim supported by cited evidence; an existing ID alone does not suffice. Empty, justified abstentions may pass when no fabricated claim is made.
- Task success: all requested items answered accurately, or justified refusal/abstention when unavailable or disallowed; no false facts or unsupported citations. A timeout, invalid answer or partial answer fails task success.
- Report reviewer identity. Codex assistant review is not independent human review; leave manual_* null. Do not represent assistant ratings as human ground truth.
- Baseline gets all the same raw runs and notes in context, one model call, no tools/repair; Agent can retrieve and calculate, max 8 model/12 tool calls. Both use temperature 0, max_tokens 1800, same model and answer schema. Tool trajectories, repair budgets and context size intentionally differ. Report cost and latency alongside ratings.
- Single run per case/strategy; no statistical superiority claim. Preserve failed attempts and missing usage. Same provider endpoint/model slug does not establish a pinned backend revision.
- Structured measurement validation does not establish natural-language entailment or completeness. Evaluation still reads prose, including limitations.

## v0.3 extension (frozen before new holdout runs)

Previous v0.2 cases are development/regression cases now. The new file benchmark-v0.3.json contains 12 new combinations, still on the same project and overlapping concepts; it is not an external independent benchmark. Two runs per case/strategy do not establish statistical significance.

Compare three controls: Agent (up to 8 calls / 12 tool invocations including submit_answer); direct (1 schema-constrained call); direct_repair (up to 8 schema-constrained calls, with the SAME source/numeric/limitation validator). Direct repair receives deterministic expected-value feedback, so it is an assisted full-context control, not unaided LLM reasoning. Raw runs and notes are identical. All use temperature 0 / max_tokens 2600. Agent has submit_answer while the direct controls use response_format; transport differs and is disclosed.

The same explicit-command read-only guard runs before all three strategies. Record policy-routed cases separately, exclude them from model-task pass denominators and cost-per-model-task denominators. Unrouted questions still use the model, and may fail to explain capabilities correctly.

Grade completeness including requested ordering and ties. Numeric values visible in structured measurements count as part of the answer. Generated limitations are disallowed in v0.3; rendered excerpts plus the fixed product boundary are not independent semantic verification of claims. Empty-set explanations can cite the deterministic query ID. Human review remains absent; manual_* stays null.

## v0.4 extension (frozen before new live cases)

Four new selection combinations, each repeated twice per strategy. Same small snapshot and overlapping concepts; no external generalization claim. v0.3 old holdout is now development evidence. The Agent now renders exact query tables in query_views instead of model-authored query prose. Review every table, parameter, ID and measurement as the delivered answer; raw model text stays in events but is not displayed as a conclusion. Wrong parameters, wrong membership or missing requested outputs fail. This changes presentation and deterministic support, so compare the complete systems, not model reasoning alone. Direct and direct_repair keep their full-context contracts and budgets. Each strategy has eight model tasks; there is no command-rule case in this set. Citation support, factuality and task success are graded separately; any citation gap also fails task success. Independent human review is absent.
# v0.5 mixed-request addition

## v0.6 planner comparison

Compare unchanged legacy single-call planner, new explicit-action planner with one call, and the same new planner with a maximum of two calls and local validation feedback. Same provider model, raw context and result rendering; prompts and schemas differ. One/two-call groups are independent runs, not paired continuations. Success still requires every requested result, correct filters, full sets, comparison direction, relevant evidence and no contradictory unhandled labels. Being executable, having all clauses mapped or passing a receipt audit is not semantic success. In particular a single clause can require two queries. Preserve invalid/partial/timeout runs without selecting replacements. Old v0.5 holdout is now development evidence; v0.6 combinations remain same-snapshot and concept-overlapping, with no independent human review.

For v0.5, computed query tables, comparison blocks and verbatim evidence/coverage are delivered answers in both the tool-loop and one-call planner systems. Check every requested part, actual conditions, all result members, comparison direction and relevant notes. A valid plan/trace alone is not success. Quote matching only proves a substring exists; it is not semantic verification. Explicitly unhandled required work is partial, not full task success, even if honestly disclosed.

The planner gets the full builtin context upfront and one proposal call; the tool-loop obtains context through calls and has up to eight model requests. Both use the same deterministic calculations and renderable evidence; this is a whole-system comparison with different budgets/context, not an isolated causal estimate of reasoning quality. Evaluation auto-execution is labelled unreviewed_evaluation and is never counted as human confirmation. Keep failures, no post-holdout tuning with fresh-score claims, and no independent human review unless actually performed.
