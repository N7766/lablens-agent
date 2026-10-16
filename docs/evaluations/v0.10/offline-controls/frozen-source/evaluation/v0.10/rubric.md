# v0.10 predeclared mixed-task evaluation

Six newly composed questions reuse two existing snapshots: the imported DPO validation summaries and the synthetic Atlas engineering fixture from v0.9. No new experiment data was measured. DPO source hashes and validation-selection notes are preserved; Atlas remains labelled synthetic. These tasks are authored with knowledge of the implementation and are not an unseen independent human benchmark.

Each question combines one query, an explicit named comparison, and one exact metadata request. The literal contract fixes selected IDs, requested sorting/filtering, baseline/candidate direction, three numeric differences, every returned row, current notes, missing fields, and snapshot provenance. Tests separately recompute the declared arithmetic. Full Pareto requests do not invent ordering requirements. Source/receipt consistency is independent of task success.

## Natural model comparison

Both arms use the unchanged v2 prompt and proposal schema, the same provider slug, temperature zero, evidence snapshot, query-rule checker, and at most two requests. `task_rules_off` leaves named-task checking disabled; `task_rules_on` adds the finite comparison/metadata checker and its feedback. Run each of six questions twice in each arm: 24 independent draws, maximum 48 provider requests. The first returned message is scored by local single-attempt replay, with zero extra provider requests. Preserve failures, timeouts, provider-reported usage, first/final scores, all returned messages, and receipt results. Independent draws and a small authored suite cannot establish a causal or general improvement.

Before constructing a provider client, persist the manifest, exact source/scorer files, snapshot bytes, literal contracts, prompt, schema, source commit, and hashes. Models see evidence plus the question, not this contract. All trials use the frozen data copy. Concurrency affects latency. The provider's internal model revision is unavailable.

## Separate injected-repair controls

Three faults are predeclared in `controls.json`: reverse an explicitly named pair; replace a metadata action with a comparison; duplicate a named comparison. Each starts from an authored positive proposal. Offline negative controls confirm that the old checker can accept the faulty plan while the independent literal contract rejects it. The new checker must detect it. Offline positive replacements verify the repair path without sampling.

`--suite controls --live` supplies the predeclared faulty first message locally and permits at most one real replacement request per control, three provider calls maximum. The seed is synthetic and costs zero. `planner_completion_calls` counts the seed and replacement; `live_provider_calls` counts real requests only. These controls never enter natural draw counts or success rates. A successful injected repair shows recovery from that specific authored fault, not natural failure frequency or general recovery ability.

Unknown wording, unknown IDs, and quoted requests are explicit abstentions and must not be presented as validated tasks. These are covered by offline tests outside the six finite-grammar cases. General semantic validation remains unperformed.
