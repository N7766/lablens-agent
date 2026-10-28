# Round 3 candidate 1 exposed regression

This is the first frozen Round 3 candidate on all six previously released Round 2 synthetic questions. No retry, no data edits, no fresh holdout access. AI developer-side grading, not independent human acceptance. Original input/gold authorship text describes their Round 2 origin only.

Runtime: 5/6 succeeded; cedar-t3 failed after six correct intended clarification actions were rejected for missing structured choices. Strict all-axis pass: 1/6; full semantic pass: 1/5 observed answers, or 1/6 planned turns. Semantic strictness includes missing requested arithmetic and missing explicit conditional outcomes; these are completeness errors, not all hallucinations.

The selected-evidence guard correctly noticed that the selected 300-character protocol fragment omitted the0.5% threshold. The full retrieved document has it. End-to-end reports still withhold answerable items; larch-t2 also retains a contradicted assessment containing the same unselected threshold. No general quality gain is claimed.

22 model calls,83564 known tokens, no unknown usage.5/5 necessary document coverage;26/26 exact quotes and12/12 cited chunk objects exist. Existence is not entailment.

See frozen input/raw manifests, runtime declaration, full HTTP/results in `run`, and per-axis reasoning in `grading/per-turn.json`. Candidate 2, if run, must be a separate explicitly authorized candidate with a new source freeze and its own package; this attempt is never overwritten.
