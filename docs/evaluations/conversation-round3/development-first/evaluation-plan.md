# Round 3 exposed development regression

This package reuses all six questions, seven synthetic documents and the original semantic rubric from the released Round 2 new-scenarios package. It is exposed development regression, AI developer-side review, not unseen, human-independent, or production evaluation. Original `inputs` / `gold` provenance text describes the Round 2 origin only; this plan and runtime metadata govern the Round 3 claim.

Freeze this plan, scope metadata, copied corpus/questions/gold, and adapted runner before inference. Do not tune source using these outputs after this frozen candidate run. Gold is never sent to the tested model. Use the existing public JVM conversation API, BM25, Agent mode, top_k=5, two conversations of three sequential turns. Maximum six task submissions and 42 model calls (six decisions plus one review per task). One attempt per turn; no automatic retries. Failed/unknown/non-successful predecessors block dependent turns and all failures remain archived. Clarification is a completed turn.

Root supplies an isolated QA endpoint and authorizes inference only after source freeze and service health validation. Record actual per-task source fingerprints, usage and full HTTP records. Immediately freeze raw files before semantic review. Then read full results and source passages and score reference resolution, semantics, citation grounding, retrieval coverage, false refusal, constraint scope, and runtime separately using the unchanged Round 2 rubric. Compare prior/current outcomes without conflating runtime, quote existence, retrieval and correctness. AI developer-side scores are not human-independent acceptance.

No production service operations, fresh holdout reads, or Git mutation are performed by this evaluator.
