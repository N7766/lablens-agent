# Admission load evidence

Variant: baseline; passed: True

Synthetic fixed-delay BM25 work; no model serving capacity or production SLA claim.

| Stage | Scheduled | Sent | Accepted | Rejected | Unsent | Transport |
|---|---:|---:|---:|---:|---:|---:|
| under | 120 | 120 | 120 | 0 | 0 | 0 |
| over | 1200 | 1200 | 319 | 881 | 0 | 0 |
| recovery | 120 | 120 | 119 | 1 | 0 | 0 |

| Check | Passed |
|---|---|
| Every scheduled slot is retained exactly once | True |
| Submission accounting balances | True |
| No unsent, transport errors or unexpected HTTP | True |
| Client concurrency is bounded | True |
| Accepted tasks have unique IDs | True |
| All accepted tasks observed SUCCEEDED | True |
| Accepted tasks have known zero provider use | True |
| Distinct requests bypass result cache | True |
| No accepted ID in rejected responses | True |
| Dispatcher max concurrency does not exceed two | True |
| Controlled worker processed every accepted task once | True |
| Activities drain to zero | True |
| Late recovery accepts without capacity rejection | True |
| Frozen scripts, protocol, RAG source and tested jar unchanged | True |

Sampled global active maximum: 32
Raw per-request records, all accepted task records and status/actuator samples are retained alongside summary.json.
