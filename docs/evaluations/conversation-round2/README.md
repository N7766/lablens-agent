# Conversation round 2 evidence

Implementation commit: `2e30611`. Local prototype, not a new official release or production acceptance.

- [Chinese acceptance report and résumé boundaries](../../conversation-round2.md)
- [Python 652 / UI 38 regressions](regressions.json)
- [JVM 60 tests, including 34 real MySQL/Redis checks](jvm-tests.json)
- [Final real HTTP cancellation faults: 4 scenarios / 35 checks](cancellation-fault-final/README.md)
- [Browser stop, history and consent checks](browser/summary.json)
- [Main database migration and unchanged historical result hashes](main-migration.json)
- [Implementation GitHub CI](implementation-ci.json)
- [Exposed development cases: separate attempts and semantic review](development-grading/README.md)
- [New frozen AI-authored cases](new-scenarios/README.md)

The first v3 candidate, its failures, the corrected candidate's startup connection failure, and an explicit CedarBend-only retest remain in separate directories. The initial cancellation fault proof and browser service control also remain alongside the final frozen-source proof. Never add counts from repeated checks as though they were distinct test cases.

Model calls use DeepInfra Qwen/Qwen3-235B-A22B-Instruct-2507. Cancellation fault providers are scripted local controls with synthetic token counts and zero external calls. New questions are authored and graded by an AI agent isolated from implementation and exposed cases; they are not human-independent evaluation. Source existence, retrieval coverage, semantic correctness, scope compliance and system completion have separate meanings.
