# development-final

Corrected candidate primary attempt. CedarBend startup connection failure is retained; HarborBridge was not rerun.

These are previously exposed, AI-authored synthetic development cases. Each runner submission is attempted once; this package is not a human-independent or new-question evaluation.

- Submitted tasks: 3; accepted answers: 1; operational failures: 2; dependent turns not run: 3.
- Strict semantic passes: 0; partial answers: 1. Missing answers are not graded as correct or as false refusals.
- The input manifest, raw task records, HTTP observations, history and output manifest are retained.

[Full rubric-based review](../development-grading/README.md) separates source coverage, conclusions, scope and false refusal. See [run summary](run/run-summary.json) for every task identity.

The first CedarBend request hit a local worker startup ConnectException. This failure is retained with conservative unknown usage. After an explicit health check, only CedarBend was manually retested in a separate package. HarborBridge was not retried.
