# Workspace RAG evaluation

Same-assistant-authored project corpus and questions. These results are not an independent benchmark, production load test, or semantic faithfulness score.

| Split | Strategy | Mode | Completed | Recall@5 | MRR@5 | Unanswerable abstentions | p50 / p95 ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| heldout | hybrid | live | 4/4 | 1.0000 | 1.0000 | 1/1 | 5089.403 / 6882.647 |

Recall denominator includes every answerable request, with failed/unavailable requests scored zero. Top 5 means retrieved chunks; duplicate chunks from one document do not create extra document credit.

Dense document warm-up and query-provider latency are recorded separately by cache condition in summary.json. Query vectors are not assumed cached. Fixed sequential strategy order and shared document cache prevent an apples-to-apples speed claim.

Source consistency checks validate cited IDs, exact source substrings, metadata and SHA-256 only. They do not judge whether a generated claim follows from the citation. Excerpts on an unanswerable question are counted as retrieval non-abstention, not fabricated factual answers.

## Recorded failures
