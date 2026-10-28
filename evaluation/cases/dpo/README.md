# DPO preference / free-generation evidence bundle

This is a retrospective, offline case from the user's `dpo-cot-compression`
research archive. It is **not** a new training run, external benchmark, model
evaluation of LabLens, or automatic causal diagnosis.

The narrative and each claim's interpretation are in
[`docs/cases/dpo-preference-generation.md`](../../../docs/cases/dpo-preference-generation.md).

## Contents

- `manifest.json`: source-relative paths, source commit, original SHA-256,
  captured snapshot hashes, and whether each source was committed research
  material or an ignored local artifact explicitly reviewed for this case.
- `sources/`: 18 source snapshots (about 503 KB total), including the complete
  462 SFT, 462 DPO, and 40 base-model generated-output records. No model weights,
  original training corpus, credentials, personal education material,
  untracked dissertation documents, or presentation files are included.
- `excerpts.json`: 28 exact source excerpts with original line numbers and hashes.
- `claims.json`: 15 analyst-reviewed statements labelled `supported`,
  `contradicted`, or `insufficient_evidence`, each with evidence references,
  rationale and scope. These are judgments, not independently authored ground truth.
- `observations.json`: arithmetic recomputed from the archived records.
- `verification.json`: the recorded verification and negative-control outcome.

`publication_provenance=ignored_local_artifact_reviewed_for_this_case` means the
original log/result was not part of the source repository's commit. Publishing
this bundle preserves its current bytes; it does not retroactively establish a
historical execution commit or authenticate the remote run.

## Reproduce

From the LabLens repository root, using Python 3.11 or newer:

```sh
python scripts/verify_dpo_case.py
python scripts/verify_dpo_case.py --self-test
# Optional: compare against an existing, read-only source checkout/archive.
python scripts/verify_dpo_case.py --source-root /path/to/dpo-cot-compression
```

The standard-library-only verifier makes **zero external requests and zero
training/model calls**. It never executes the source snippets. It checks:

1. Every captured file and declared artifact hash.
2. Exact excerpt text, line ranges, and claim-to-evidence references.
3. All 964 generated outputs using a transcription of the included answer
   parser: extracted answer, Boolean correctness and `len(prediction.split())`.
4. SFT/DPO ID and gold-answer alignment, all summaries, the 40-item base subset,
   and trainer-state observations.
5. With `--self-test`, five parser examples and five negative controls:
   changed bytes, a rehashed but incorrect summary, a rehashed incorrect excerpt,
   a missing evidence reference, and an escaping path.

The verifier does **not** certify a claim's natural-language truth, historical
code identity, source authenticity, data cleanliness, or causal explanation.
Hashes are not signatures; self-consistent changes to data and hashes can form
another bundle. The source code snapshots represent captured current code,
while training/generation artifacts are historical local records.

## Supported interpretation

Training preference scores improve in the Stage 4 log, final preference
validation accuracy is 1.0, and its recorded free-generation score is 153/462.
The SFT comparator is 439/462, but **Stage 4 is recorded as direct full DPO from
the base model**, not continuation of that SFT model. The base model already
scores 13/40 under a short-budget evaluation; Stage 4 scores 12/40 on those IDs.
These facts support a metric/behavior mismatch and an investigation, not a
single-cause conclusion that DPO destroyed previously 95%-accurate weights.
