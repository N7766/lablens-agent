# Frozen semantic scoring rubric — v0.13 external-question adaptation

This is a blinded AI pre-acceptance check, not an independent human evaluation.
The evaluator selected real public questions and authored the adaptations and
gold labels. The same evaluator worked on v0.12 evaluation/system tests, but has
not read or implemented the new v0.13 semantic repair. External question authors
did not author the adaptations, run this project, or endorse its results.

## Frozen design

- Twelve questions, four each with supported, contradicted and insufficient gold
  outcomes. Contradicted means an answerable false premise with positive opposing
  evidence. Insufficient means the requested measurement or conclusion is absent
  from this finite corpus. It does not assert that the measurement never existed.
- Public project sections/configuration and attributed evaluator paraphrases of
  original external discussions form the corpus. Public repository commit and
  text hashes are retained. Controlled wrong premises appear only in questions,
  not as fabricated experiment reports.
- All questions use the same full corpus; only the question changes. BM25, top five
  chunks, live mode, one HTTP draw per question. The parent chose BM25 before any
  outputs so this pass can isolate retrieval/answer quality without embedding cost.
- Twelve extractive preflight requests are allowed solely for schema and retrieval
  observation. They must not trigger implementation, corpus, gold, or prompt tuning.
  Then twelve live requests, each permitting the service's fixed draft plus review
  calls: maximum twenty-four generation calls. No failed case is rerun or replaced.
- Raw requests/responses, transport errors, source/runtime fingerprints, token and
  call counters, latencies and evaluator explanations are retained.

## Unit of semantic judgment

Judge the question's meaning and every material proposition in the final answer
and structured assessments together. API status, assessment labels, literal quote
presence and document IDs are diagnostic data, never semantic truth labels.
An answer can say a false premise is contradicted without refusing to answer.
Recognizing absent numerical measurements can be the correct substantive answer.
Reasonable paraphrases and directly justified arithmetic are accepted. Explain
arithmetic and scope. Do not demand identical wording or exact citation ordering.

## A. Retrieval evidence coverage

For each frozen `must_establish` requirement, read the returned hit text and mark:
1 = enough evidence or a directly justified inference; 0.5 = partial supporting
context but a necessary fact/scope missing; 0 = no adequate supporting evidence.
Report the requirement-level mean and whether all requirements have full coverage.
The document groups are reviewer navigation aids, not a substring or ID success
test; another document with equivalent evidence is acceptable. Seeing a relevant
document title without the required passage is not full coverage. For absence
questions, score whether the hits expose the available scope and its measurement
limit; only the full frozen-corpus audit establishes that requested data is absent
from the supplied corpus. The absence of a fact among five hits alone proves none.

## B. Conclusion correctness

Score each case against the frozen gold and all `must_establish` requirements:
- 2: correct central conclusion, all requested material facts and scope preserved,
  no material contradiction with evidence. Explicitly limited unknowns are correct.
- 1: central direction is correct but a requested fact/qualification is omitted,
  or the output is internally inconsistent without reversing the main conclusion.
- 0: central conclusion wrong, requested numbers invented, false premise accepted,
  unqualified refusal despite sufficient corpus evidence, or no usable output.

Report strict 2/2 case rate, score average and outcome-stratified results. Mechanical
assessment label accuracy is secondary: the service may decompose a question into
multiple statements, so labels are interpreted in relation to the statements they
label. For example, a correct negation can itself be `supported` while contradicting
the user's premise. Do not force every assessment to equal the whole-question gold.

## C. False refusal

The denominator is the eight corpus-answerable supported/contradicted cases.
Mark a false refusal if the final output withholds the requested central answer as
unknown/unanswerable, or gives no usable answer, although the frozen corpus contains
it. A correction of a false premise is not refusal. A qualification about runtime
verification is not refusal when the requested documented fact is still answered.
Report corpus-level false refusal and, separately, false refusal when the actual
retrieved hits were sufficient. This separates retrieval-induced misses from a
generator declining adequate retrieved evidence. A failed transport/provider call
is an operational failure, not semantic refusal; retain it in the all-12 correctness
denominator but exclude it from a completed-output semantic-refusal denominator.

## D. Unsupported claims and citation entailment

Split material factual assertions into atomic propositions. Judge each against:
1. the complete frozen corpus (corpus-supported / contradicted / absent), and
2. the passages actually cited for that proposition (entailed / partial / unsupported /
   no citation). Source titles, matching text, valid offsets and hashes prove provenance
   only; they do not establish entailment of an altered statistic, time, entity,
   causal claim, measurement scope or final-test conclusion.

Count material unsupported-or-contradicted propositions and affected answers, with
a case-specific explanation. Correct conclusions lacking a supporting citation are
reported separately from factually false conclusions. Extra speculative advice is
acceptable only when explicitly framed as a proposed next check, not a measured
project fact. Never award correctness for fabricated values merely because they
could theoretically be true. Do not count restating the user's false premise for
the purpose of rejecting it as the model asserting that premise.

## E. Failure attribution and uncertainty

Assign zero or more: retrieval_missing_evidence, wrong_inference,
scope_or_metric_conflation, false_refusal_with_evidence,
unsupported_or_contradictory_claim, citation_non_entailment,
malformed_output, provider_or_transport_error. Give the observable reason without
claiming to know internal model causality. Distinguish gold verdict from observed
status and model self-review; self-review is not external semantic certification.
Report every failed/partial case. Small purposively selected AI-adapted questions
cannot establish population generalization or independent human acceptance.

## Freeze discipline

The corpus, question wording, labels, expected facts, source provenance and this
rubric are frozen before any endpoint responses. If the evaluator discovers a
gold/source error later, preserve the original, label the affected item invalid,
explain the correction separately and report both original and valid-item counts.
Do not silently change a gold label to fit a model answer. No acceptance tuning on
these twelve questions is allowed before the one-shot outputs are all fixed.
