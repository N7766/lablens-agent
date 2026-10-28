# Workspace RAG evaluation

Same-assistant-authored project corpus and questions. These results are not an independent benchmark, production load test, or semantic faithfulness score.

| Split | Strategy | Mode | Completed | Recall@5 | MRR@5 | Unanswerable abstentions | p50 / p95 ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| dev | bm25 | extractive | 8/8 | 0.8333 | 0.8333 | 0/2 | 2.279 / 9.888 |
| dev | dense | extractive | 8/8 | 1.0000 | 1.0000 | 0/2 | 955.455 / 3177.995 |
| dev | hybrid | extractive | 8/8 | 1.0000 | 0.8889 | 0/2 | 1047.552 / 1541.21 |
| heldout | bm25 | extractive | 16/16 | 0.7083 | 0.7500 | 0/4 | 2.205 / 2.458 |
| heldout | dense | extractive | 16/16 | 1.0000 | 1.0000 | 0/4 | 1024.947 / 1622.043 |
| heldout | hybrid | extractive | 16/16 | 0.8750 | 0.8750 | 0/4 | 1022.982 / 1536.507 |

Recall denominator includes every answerable request, with failed/unavailable requests scored zero. Top 5 means retrieved chunks; duplicate chunks from one document do not create extra document credit.

Dense document warm-up and query-provider latency are recorded separately by cache condition in summary.json. Query vectors are not assumed cached. Fixed sequential strategy order and shared document cache prevent an apples-to-apples speed claim.

Source consistency checks validate cited IDs, exact source substrings, metadata and SHA-256 only. They do not judge whether a generated claim follows from the citation. Excerpts on an unanswerable question are counted as retrieval non-abstention, not fabricated factual answers.

## Recorded failures

- dev/bm25/extractive/d03: missing_required_document; missing=['dpo:direct-result']; status=excerpts
- dev/bm25/extractive/d07: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- dev/bm25/extractive/d08: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/bm25/extractive/h05: missing_required_document; missing=['dpo:chosen']; status=excerpts
- heldout/bm25/extractive/h06: missing_required_document; missing=['dpo:sft-runtime']; status=excerpts
- heldout/bm25/extractive/h07: missing_required_document; missing=['dpo:full-followup']; status=excerpts
- heldout/bm25/extractive/h12: missing_required_document; missing=['dpo:final-protocol']; status=excerpts
- heldout/bm25/extractive/h13: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/bm25/extractive/h14: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/bm25/extractive/h15: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/bm25/extractive/h16: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- dev/dense/extractive/d07: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- dev/dense/extractive/d08: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/dense/extractive/h13: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/dense/extractive/h14: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/dense/extractive/h15: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/dense/extractive/h16: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- dev/hybrid/extractive/d07: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- dev/hybrid/extractive/d08: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/hybrid/extractive/h05: missing_required_document; missing=['dpo:chosen']; status=excerpts
- heldout/hybrid/extractive/h06: missing_required_document; missing=['dpo:sft-runtime']; status=excerpts
- heldout/hybrid/extractive/h13: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/hybrid/extractive/h14: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/hybrid/extractive/h15: returned_output_on_unanswerable_question; missing=[]; status=excerpts
- heldout/hybrid/extractive/h16: returned_output_on_unanswerable_question; missing=[]; status=excerpts
