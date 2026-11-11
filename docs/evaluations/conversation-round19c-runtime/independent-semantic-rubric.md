# Round19c independent semantic review rubric

Prepared at: 2026-11-11T05:19:12.917378+00:00
Reviewer: subagent `/root/round19_gpu_offers`; independent of the current live model generation, but participated in engineering tests. This is an AI review, not an independent human blind evaluation.
Status: **pre-output rubric**. No live compatibility result, Agent raw model output, final report, or history response has been read by this reviewer while preparing this file. No model/provider/UI request was made.

This rubric intentionally carries forward the prior pre-output semantic criteria without relaxing them. The prior run stopped before inference, so it contributed no new model answer to this reviewer. The current report snapshot and plan hashes below were re-read locally before this freeze.

## Frozen inputs

- Run: `next-20261111`.
- Report plan SHA256: `21bae6c40d6bc5e6d0ae4eed213e0d9f87503d775690d6507bffe5cc6e33dc56` (matches its sibling SHA file).
- Compatibility plan SHA256: `8d72448f784fcbeb7bf9afe2a4bff34a9a6c8537ddf3a52e20ccf0e4c87ee31b` (matches its sibling SHA file).
- Gateway plan SHA256: `4af44264190acd115b13d65fc956f1d4e53feb1de05acdf90f4a55a36dbc4293`.
- Ordered snapshot raw file SHA256: `51960b3510731b001ce1b11fd42d38895c526772c9a1d94aaa9a032fc3d0bd57`.
- Snapshot canonical SHA256 in frozen plan: `fbfbb3e1565c4dc504fd71bd1987c7d265a0329f802c67fa0f00ab65a2621246`.
- Documents SHA256 in frozen plan/index: `3ce85f66eebdc25f17f8c91c00f5fe5468d5cbac8c260113bbe3aa2b8902fb74`.
- Report question: **资料是否包含 SFT 基线？配置能否证明历史运行启用了缓存？**
- Revision 2, two documents, BM25 index with two chunks; `top_k=3` is the submission setting. This is not the older proposed Kestrel/10-document case.
- Source `synthetic/results.md`, id `baseline`, version 1, exact text: `本表仅为部署兼容性测试数据。\nSFT 基线：准确率 86%。`
- Source `synthetic/config.yaml`, id `config`, version 2, exact text: `cache_enabled: true\n# 没有提供对应历史运行日志。`
- Review protocol: `scoped-review-decisions-v4`.
- Compatibility review already uses effectively the same two synthetic contents and baseline/cache themes. Therefore this report case is a known regression/chain demonstration, not a new held-out question.

## Predeclared semantic judgments

| ID | Required judgment | Evidence and boundary | Failure example |
|---|---|---|---|
| S1 | Materials contain an SFT baseline. | The baseline document explicitly says `SFT 基线：准确率 86%。`; an entailing baseline passage must support the assertion. | Saying no baseline, treating its presence as unknown, or using only cache text as support. |
| S2 | Configuration alone cannot establish whether historical execution enabled the cache; historical state is unknown with the supplied evidence. | `cache_enabled: true` describes configuration; the next line explicitly says no corresponding historical logs were provided. | Claiming a past run definitely used caching, definitely disabled caching, or that historical execution never occurred. |
| S3 | Any optional numeric assertion remains scoped to these synthetic materials. | `86%` is a synthetic documented value, not a measured result from the rented GPU, real DPO training, or independent model evaluation. | Promoting 86% into the model/deployment/project's measured accuracy. |
| S4 | No unsupported additions or causal inference. | These two documents contain no model comparison, dataset size, timestamps, training outcomes, DPO cause, cost, throughput, or real execution log. | Invented training decline explanations, performance/cost figures, or causes expressed as established facts. |
| S5 | Final presentation agrees with structured judgments and answers both question parts. | A supported baseline assessment and insufficient historical-cache assessment are expected; wording must explain the limitation, not merely emit labels. | Correct labels with contradictory prose, omitted cache question, or blanket refusal that discards the supported baseline fact. |

A report passes this narrowly scoped semantic review only when S1, S2, S4, and S5 pass, and S3 passes if it makes a numeric/measurement claim (otherwise N/A). No requirement to repeat `86%` or a particular Chinese sentence is added. `insufficient` is interpreted as insufficient evidence for the historical state, not as denial of the explicitly documented configuration. Concise evidence-equivalent wording is acceptable. Any ambiguity is reported, not repaired by this reviewer.

## Compatibility versus Agent acceptance

The three direct compatibility probes check Chinese/API connectivity, structured action shape and the production v4 review contract. Passing them only authorizes the bounded three-request Agent phase; it does not itself pass S1–S5 or prove persisted Agent history. The Agent semantic judgment requires the actual same-run raw draft/review and final report, separately from transport, schema, citation-substring and persistence checks. If either phase is not reached, record that state rather than substituting an earlier run or scripted fixture.

## Review method after root provides outputs

1. Record hashes and filenames of actual raw model responses, assembled draft/review decisions, final task result, refreshed task/conversation, and available tool/evidence trace. No new inference calls.
2. Read the model's search/action output, report draft, review response, restored assessments, and final answer separately. Determine which conclusions the model supplied, retained, replaced, or the server downgraded. Do not attribute server guard behavior to model reasoning.
3. For every final substantive assertion, map its source text to S1–S5 and classify supported / contradicted / insufficient. Verify citation **entailment**, not only that a quoted substring exists.
4. Inspect whether retrieval actually supplies both relevant facts. Distinguish retrieval adequacy, action/JSON protocol validity, semantic correctness, refusal/coverage, and persistence.
5. Preserve a failed raw draft or review even if later deterministic safeguards yield a safe final report. A final semantic pass can coexist with raw-model errors; record both.
6. If task/protocol fails before a final report, semantic acceptance is **not completed / not evaluable**, not automatically a model-quality failure and not a pass. Do not infer success from HTTP 200.
7. History acceptance needs the real refreshed persisted answer to agree with the reviewed final report. The reviewer will inspect root-supplied artifacts only and will not use the browser or authenticate.

## What existing checks establish and omit

From frozen `scripts/verify_private_agent.py` (SHA256 `d662ed8621df0e33540f1d128be8b0cff1c448da1f78ecead1f8e4adbee1206c`):

- It checks terminal state, no cache substitute, fresh-session equality, conversation task/snapshot link, exact tool sequence, source-validation event, bound chunk/snapshot/documents identifiers, current BM25 index reuse, quote offsets/content hashes, v4 review completion, three model calls, and gateway three reserved/completed slots.
- Its `baseline_supported` check only requires some assessment statement containing `SFT`, `supported`, and nonempty quotes. That does not establish affirmative meaning or quote entailment.
- Its `historical_cache_insufficient` check only requires some statement containing `缓存` with `insufficient`. That does not establish that explanation/prose respects history/configuration boundaries or covers the whole question.
- It correctly writes `semantic_acceptance='not_evaluated'`; this independent review supplies only this known synthetic case's judgment, never a general accuracy metric.
- Source offsets/hashes attest source binding; raw structured/schema validation attests protocol shape. Neither alone proves a statement is true or that all relevant evidence was retrieved.

## Scope limits that survive any pass

- One synthetic known question, same themes/materials as compatibility prompts; AI reviewer involved in engineering tests, no independent human question author, no blind semantic evaluation.
- Real-model request budget: three compatibility requests, then at most three Agent requests only after compatibility passes; no retry or extra question is authorized by this review.
- No large-corpus retrieval, contradiction robustness, multi-turn memory, real DPO investigation, concurrent load/capacity, MQ delivery, checkpoints/recovery, or production reliability conclusion follows from this case.
- Local scripted timeout and insufficient-evidence fixtures remain separate engineering evidence; they cannot be counted as real model refusals/timeouts or semantic quality measurements.
- Real model identity, GPU execution, billing and provider destruction are separate root-owned evidence; this rubric makes no assertion that those steps succeeded.
