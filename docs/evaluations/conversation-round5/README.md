# Conversation Round5 evidence

Full narrative: [界面精简与可见证据去重](../../conversation-round5.md).

- [Candidate freeze](candidate-freeze.json): model runs bind to `6f79300` / Agent v6.
- [Deployment freeze](deployment-freeze.json): later HTML asset-version fix only; runtime source unchanged.
- [Publication integrity audit](artifact-audit.json): source, sealed inputs/results, screenshots and offline rescore.
- [Engineering regressions](regressions.json), [source CI](implementation-ci.json), [scoped review](source-review.md).
- [Decision payload reconstruction](decision-payload-comparison.json): offline byte comparison; no speed claim.
- [Exposed development regression](development/REPORT.md): delivered 6/6, strict 2/6.
- [New frozen scenarios](new-scenarios/README.md): delivered 2/4, strict 0/4; one timeout and one blocked turn.
- [Controlled runtime faults](runtime-fault/README.md): 64/64 checks, zero external inference.
- [QA cleanup](qa-cleanup.json): only owned idle QA processes stopped; retained database hashes and main services verified.
- [Browser evidence](browser/summary.json), [main upgrade](main-upgrade.json), [HTML cache-fix upgrade](main-upgrade-cache-fix.json).

Engineering completion, delivery, retrieval and semantic quality are separate. This is local prototype/synthetic evidence, not production capacity or human-independent validation.
