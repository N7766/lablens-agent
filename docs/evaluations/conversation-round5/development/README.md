# Round5 exposed development regression

Read [REPORT.md](REPORT.md) for interpretation, [grading/grades.json](grading/grades.json) for all six frozen-gold grades, and [run/run-summary.json](run/run-summary.json) for the immutable execution record.

This package uses already exposed Round3 synthetic cases. The run has six delivered outputs but only two strict passes. It is AI developer grading, not a human-independent holdout or a production benchmark. Old holdout wording inside frozen inputs is historical provenance and is not the scope of this run.

- [Frozen inputs](frozen-manifest.json), [original gold](gold/rubric.json), [protocol](protocol.md)
- [Candidate identity](code-freeze.json), [approved runner](runner.py), [raw output seal](raw-output-manifest.json)
- [Request and stage audit](grading/runtime-audit.json), [failure diagnostics](grading/failure-diagnostics.json)
- [Pre-run checks](source-precheck.json), [post-run checks](source-postcheck.json), [runner log](runner.log)

No retry, rewritten answer, modified gold, or candidate patch is included in these results.
