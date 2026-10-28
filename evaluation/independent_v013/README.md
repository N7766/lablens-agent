# External-question AI blind pre-acceptance evaluation

Two one-shot candidate runs and manual AI semantic reviews are complete. Read
[RESULTS.md](RESULTS.md) for metrics, failures and scope limitations. This did not
establish acceptance: strict complete answers were 2/12 and 1/12, with retrieval
missing the English project documents in both runs. Mechanical provenance checks
passing did not establish citation entailment.

The twelve questions, corpus, source attribution, gold labels and semantic rubric
were frozen before any endpoint response on 2026-10-28 at 09:24:59 UTC.

Suite SHA256: `c5f8cd2ba00ecef1d0a7d264ca743f5b9656c44f79859b91f8774c4c18908dd5`.

This is an AI evaluator's adaptation of eight original public GitHub/Hugging Face
question threads. External authors did not review this project or these adapted
questions. The evaluator worked on v0.12 evaluation, but did not read or develop
the v0.13 semantic repair before this frozen test. It is not independent human
acceptance or complete project independence.

The suite stayed in ignored `.local/independent-v013` until both one-shot outputs
and semantic reviews were sealed; it is now available in [suite](suite). Four
supported, four contradicted and four insufficient cases use the
same twenty-five-document public-source corpus. Wrong premises are explicitly
labeled controlled questions; no invented results are presented as experiments.

BM25/top-five retrieval is fixed. A twelve-request extractive schema preflight is
followed by twelve live requests, with at most twenty-four draft/review model calls
and no retries. Preflight data is not used for implementation tuning. The capture
script does not grade semantics; the subsequent AI review separately judges evidence
coverage, conclusion correctness, false refusal, unsupported claims and citation
entailment using the previously frozen rubric. Valid quote offsets alone cannot
establish any of these semantic outcomes.

Run the capture utility's local invariant checks with:

```bash
python scripts/evaluate_independent_v013.py --self-test
```

Full execution needs the frozen suite and a runtime fingerprint for the candidate.
It records every raw request/response, failure, latency and reported token/call
count. Failed outputs remain in the expected-case denominator. The second arm was
predeclared using only old development-case findings, while held-out questions and
results were withheld. It used another 12 requests / 24 model calls; both arms are
retained. After publication, this suite is a regression set, not a fresh holdout.

The public data-only `suite/freeze.json` excludes a private build helper containing
a local source path. Actual corpus, questions, source attribution and rubric are
byte-identical to the original frozen files; `suite/original-freeze.json` retains
the original complete hash record. Public replay verifies the data-only manifest.
