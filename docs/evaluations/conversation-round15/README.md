# Round15 evidence index

Candidate source commit: `6aa1190` (implementation and frozen protocols). Source fingerprints and the tested JVM JAR are verified in [source-verification.json](source-verification.json). Later delivery commits add evidence and documentation, not rescored model outputs.

- [Frozen retrieval protocol](protocol.md), [corpus](corpus.json), [questions](questions.json), [actual ordered snapshot](run/ordered-snapshot-freeze.json).
- [Retrieval summary](run/retrieval-summary.json), [per-case metrics](run/retrieval-rows.json), [embedding reservations and observed usage](run/embedding-call-ledger.json).
- [Restart protocol](restart-protocol.md), [owned process events](restart-process.jsonl), [post-restart dense response](run/restart-dense-query.json), [extra query usage](run/restart-embedding-ledger.json).
- [Revision/deletion invalidation](run/invalidation-summary.json), on a separate engineering workspace; frozen retrieval documents remain unchanged.
- [Single Agent protocol](agent-protocol.md), [failed task and tool trace](run/agent-final.json), [fresh history readback](run/agent-refreshed.json), [independent-task AI review](agent-review.md). This run did not produce a report; semantic correctness and false refusal were not evaluated.
- [Validation logs](validation/): 1964 Python passes; 167 Java passes with2 conditional broker-restart cases skipped;83 UI passes. Initial privileged-trigger fixture failure remains beside the corrected constraint-fault tests.
- [Browser evidence](browser/summary.json): a separate two-file import and free BM25 build; failed Agent history before/after reload,204 captured GET requests,zero console errors/warnings. No new provider call from the browser.
- [Next-round handoff](next-round-handoff.md). Main services were not promoted/restarted; isolated services are stopped and test databases preserved. [Cleanup evidence](cleanup.json).

`run/` contains exact HTTP request/response records without credentials. The 118 polling observations are losslessly gzip-compressed individually; [compression-manifest.json](compression-manifest.json) records original SHA256 values. Final task JSON is uncompressed. Local SQLite/private process configuration is not published.

To reproduce, use the supported service configuration and an isolated database/Redis/RabbitMQ namespace, build the JVM, set a private `LABLENS_INDEX_DB`, then execute `scripts/evaluate_persistent_index_round15.py prepare|retrieval|restart|agent|invalidate --output <new-dir>`. Paid phases require `--go GO`. Stop/restart the worker before the restart phase, keeping its index file. `harness/` records this machine's orchestration with local tool paths; it is evidence, not a portable one-command setup. Do not replay a sealed output directory. Changing model, corpus, budgets or code creates a new experiment identity.

A 10-document/12-question synthetic fixture cannot prove broad RAG quality or production capacity. All three retrieval strategies saturated this fixture; no strategy improvement follows. The Agent's first request used2410 known tokens, its second request timed out with unknown usage. Total model cost cannot be calculated. Embedding used1984 known tokens across retrieval/build and restart probe; its price estimate is not an invoice.
