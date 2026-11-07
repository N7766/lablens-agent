# Round15 frozen retrieval protocol

Frozen at **2026-11-07T21:20:56Z** (2026-11-08 Asia/Shanghai). Protocol identifier: `round15-kestrel-retrieval-v1`.

## Provenance and freeze

The separate evaluation task authored these fixtures before inspecting the Round15 retrieval implementation. The parent task selected the engineering scope. Both tasks are AI agents: this is an **AI-authored synthetic regression set**, not an independently authored human evaluation, external benchmark, real DPO investigation, or a blind acceptance test. Every Kestrel run, measurement, date and file path is fictional. Existing failures from earlier rounds remain unchanged.

| Frozen file | SHA-256 of exact UTF-8 file bytes | Contents |
| --- | --- | --- |
| `corpus.json` | `d140576b9e1155d039bd509b564183a2631924b10d187da2aa7f0e2526e084ab` | `documents`: 10 objects with `id`, `title`, `text`, `source`; 1,969 document-text characters; each text <= 241 characters |
| `questions.json` | `1bc4c94ae9eb2e97658ecdbe31c377721524dcc41a4059c4de53f65f5e98dbb9` | `questions`: 12 objects with question, relevant document IDs, expected evidence status, required facts, forbidden claims; two contradiction questions |

Hash both files before and after each run. Candidate code identity, loaded implementation hash, all evaluation scripts, environment settings without secrets, request order, and index-manifest identity must be sealed before provider calls. Fixtures and gold labels must not be edited in response to results. A necessary protocol or candidate correction creates a new run identity and leaves the original raw run and failure visible.

## Retrieval procedure

1. Import the exact ordered documents into a fresh isolated workspace and record workspace ID, revision, import identity and snapshot/document hash. The import result and index readiness are different states.
2. Build the persistent index for this snapshot. Record chunk count, chunk/content hashes, chunking algorithm/configuration, embedding provider/model/local revision namespace, dimension, usage, build identity and terminal state. Never use a partly built index.
3. Restart the worker and show the ready index is reused. All tested retrieval calls use `mode=extractive`, `top_k=3`, unchanged question text, and the same workspace snapshot. An embedding query call is permitted; generation is not.
4. Execute questions in file order once for each strategy, with strategy order fixed to `bm25`, `dense`, `hybrid`. Do not add tuning questions, change weights or rerun failed provider calls inside this run. Preserve the complete returned top-three hits, ranks, scores, provenance, timings, usage and error status. An API/provider failure is an error case, not an empty successful retrieval and not a semantic refusal.
5. Modification/deletion/rebuild/recovery checks use a separately named engineering workspace/run. They must not mutate the frozen corpus or silently replace the retrieval run's snapshot.

All ten texts fit one existing 800-character chunk each; confirm actual chunk counts before the run. The design intentionally concentrates on evidence selection and version identity rather than long-document chunk-boundary quality. Exact-scan retrieval on this tiny corpus provides no ANN, large-corpus throughput or production traffic claim. Report raw order-dependent latency; do not claim a strategy speed advantage from this single fixed-order run.

## Metrics and acceptance boundaries

Let `G(q)` be the gold relevant document-ID set and `H(q)` be the unique document IDs among the first three returned chunks. A repeated chunk from the same document contributes at most one relevant document.

- **Recall@3:** `|G(q) ∩ H(q)| / |G(q)|`; report macro mean across all 12 questions, per-question values and number of questions with complete relevant-document coverage. Failed retrieval calls score zero in the all-planned denominator and are additionally reported as API failures.
- **Precision@3:** `|G(q) ∩ H(q)| / 3`; denominator remains three if fewer hits are returned. These gold sets often contain only one relevant document, so perfect retrieval does not imply precision of one. Report macro mean and each question's theoretical maximum `min(3, |G(q)|) / 3`.
- **Contradictory-pair coverage@3:** both IDs in `contradictory_pair` must appear in the same response's top three. Report the two questions individually and aggregate as `covered / 2`; retrieving one side or an intermediary summary does not count.
- **Engineering gates:** corpus/index identity retained across restart, zero document re-embedding on ready-index reuse, no old-revision hits after edit/delete, no partially built index accepted, explicit terminal states for cancellation/failure, and durable import idempotency. Report each independently, with exact harness assertions and raw records.
- **Evidence-status classification and answer correctness are NOT measured by this retrieval run.** `expected_evidence_status` (`supported`, `contradictory`, `insufficient`) is a gold annotation for a later semantic evaluation. Presence of a source citation, nonempty quotes or successful HTTP does not establish the status label or correct answer. Do not infer a semantic pass from these annotations or from retrieval coverage.

This comparison is descriptive: no post-hoc choice of winner, quality threshold or claim that dense/hybrid must beat BM25. Report regressions and failure cases. A separate bounded Agent demonstration may use `q02-sft-present` to verify tool→retrieval→report→history wiring, but it is an exposed fixture and must have a distinct generation protocol, ledger, semantic review and run identity. It is not part of the no-generation retrieval scores.

## Provider, version limits and budget

Official pages checked on 2026-11-08 identify `BAAI/bge-m3` as an available multilingual embedding model, advertise an 8,192-token context and standard price **US$0.010 per 1,000,000 input tokens**, and show an OpenAI-compatible request using model name and `encoding_format=float`. [Official model/API page](https://deepinfra.com/BAAI/bge-m3/api), [official embeddings API](https://docs.deepinfra.com/apis/embeddings), [official pricing](https://deepinfra.com/pricing).

- Endpoint: `https://api.deepinfra.com/v1/openai/embeddings`; model: `BAAI/bge-m3`; `encoding_format=float`; standard service (no priority tier).
- Use local cache namespace `deepinfra-bge-m3-observed-2026-11-08` and record `provider_revision_pinned=false`. This is an observation/cache namespace, **not an immutable provider weight revision**. The checked embeddings API documents the model name but does not establish an immutable revision guarantee. Do not infer embedding revision support from chat-completion version syntax. Persist returned model metadata and vector dimension; a change invalidates reuse.
- Upper bounds across all provider calls in this retrieval run: **60 HTTP requests, 250 embedded input strings, 50,000 accounted/reserved input tokens, US$0.05, 300 seconds wall time**. Per HTTP request timeout **20 seconds**, automatic retries **0**, concurrency **1**. Embed documents in one batch when possible, then one query per dense/hybrid question. No LLM generation calls.
- The listed standard price implies at most **US$0.0005** for 50,000 reported input tokens; US$0.05 is a separate hard safety ceiling. Record actual returned usage and calculate estimates using the verified price; this is not a settled supplier invoice.
- Before sending, reserve a conservative token allowance for each input from its UTF-8 byte length, with minimum one token per string; if the chosen tokenizer/provider contract cannot justify this bound, stop before the call. Enforce request/input/time/cost caps before dispatch, not only in the final summary. Use a call ledger with request ID, stage, input count, reservation, start/end, outcome and usage availability; never store keys or authorization headers.
- On send/timeout/provider failure, retain the attempt, mark usage unknown when unavailable, and stop new paid work. Never count unknown usage as zero, silently retry, switch model, reduce questions, or replace output with mock vectors. A stopped run is incomplete, with explicit planned/completed/failed counts.

## Publication requirements

Publish raw sanitized responses and a manifest, metric definitions, per-question results, aggregate denominators, revision/restart checks, provider usage and limitations. Keep performance, retrieval, tool-chain wiring and semantic conclusions separate in the README and résumé notes. Mock embeddings are allowed for deterministic unit/fault tests only and must be identified as mocks; they cannot supply the reported dense/hybrid retrieval comparison.
