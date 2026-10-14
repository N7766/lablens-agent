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
