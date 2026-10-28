#!/usr/bin/env python3
"""Verify the frozen DPO diagnostic case, without training, network, or model imports.

Checks hashes, exact source excerpts, references, and arithmetic. Claim verdicts
are documented analyst judgments, not semantic conclusions certified by this
script. Hashes establish internal consistency, not authenticity or a signature.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile


DEFAULT_BUNDLE = Path(__file__).resolve().parents[1] / "evaluation/cases/dpo"
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts, "Unsafe relative path")
    result = (root / rel).resolve()
    require(result.is_relative_to(root.resolve()), "Path escapes its evidence root")
    return result


def normalize_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def extract_answer(text):
    """Frozen scoring policy, transcribed from the included reviewed source.

    This reproduces the existing policy, including its permissive search after
    the last Answer tag. It is not a claim that this is the best scoring policy.
    Source text in the evidence bundle is never executed.
    """
    if not text:
        return None
    tags = list(re.finditer(r"answer\s*:", text, flags=re.IGNORECASE))
    if tags:
        match = NUMBER_RE.search(text[tags[-1].end():])
        if match:
            return normalize_number(match.group(0))
    finals = list(re.finditer(
        r"final\s+answer\s+(?:is|=|:)\s*([-+]?\d[\d,]*(?:\.\d+)?)",
        text, flags=re.IGNORECASE))
    if finals:
        return normalize_number(finals[-1].group(1))
    if "####" in text:
        match = NUMBER_RE.search(text.split("####")[-1])
        if match:
            return normalize_number(match.group(0))
    matches = NUMBER_RE.findall(text)
    return normalize_number(matches[-1]) if matches else None


def generation_metrics(data):
    outputs, summary = data["outputs"], data["summary"]
    require(outputs and len({r["id"] for r in outputs}) == len(outputs), "Duplicate/empty generation IDs")
    mismatches = Counter(pred_answer=0, is_correct=0, output_tokens=0)
    correct = 0
    lengths = []
    for row in outputs:
        answer = extract_answer(row["prediction"])
        gold = normalize_number(row["gold_answer"])
        matches = answer is not None and gold is not None and answer == gold
        words = len(row["prediction"].split())
        mismatches["pred_answer"] += answer != row["pred_answer"]
        mismatches["is_correct"] += type(row["is_correct"]) is not bool or matches != row["is_correct"]
        mismatches["output_tokens"] += type(row["output_tokens"]) is not int or words != row["output_tokens"]
        correct += matches
        lengths.append(words)
    require(not any(mismatches.values()), f"Saved output fields do not reproduce: {dict(mismatches)}")
    calculated = dict(num_samples=len(outputs), correct=correct, accuracy=correct / len(outputs),
                      avg_output_tokens=sum(lengths) / len(outputs))
    for key, value in calculated.items():
        require(math.isclose(summary[key], value, rel_tol=0, abs_tol=1e-12), f"Generation summary mismatch: {key}")
    return dict(num_samples=len(outputs), correct=correct, accuracy=correct / len(outputs),
                mean_whitespace_words=sum(lengths) / len(outputs),
                max_new_tokens=summary["max_new_tokens"], batch_size=summary["batch_size"],
                saved_field_mismatches=dict(mismatches))


def derive_observations(sources):
    sft, dpo, base = (json.loads(sources[k]) for k in ("sft_outputs", "dpo_outputs", "base_outputs"))
    generations = {key: generation_metrics(data) for key, data in (("sft", sft), ("dpo", dpo), ("base40", base))}
    identity = lambda row: (row["id"], normalize_number(row["gold_answer"]))
    require([identity(r) for r in sft["outputs"]] == [identity(r) for r in dpo["outputs"]], "SFT/DPO IDs or gold differ")
    require(sft["summary"]["eval_file"] == dpo["summary"]["eval_file"] == base["summary"]["eval_file"], "Evaluation file labels differ")
    paired = Counter(both_correct=0, sft_only=0, dpo_only=0, both_wrong=0)
    for left, right in zip(sft["outputs"], dpo["outputs"]):
        key = ("both_correct" if left["is_correct"] and right["is_correct"] else
               "sft_only" if left["is_correct"] else "dpo_only" if right["is_correct"] else "both_wrong")
        paired[key] += 1
    dpo_by_id = {r["id"]: r for r in dpo["outputs"]}
    require(all(r["id"] in dpo_by_id and identity(r) == identity(dpo_by_id[r["id"]]) for r in base["outputs"]), "Base subset IDs/gold do not align")
    state = json.loads(sources["dpo_state"])
    train = [r for r in state["log_history"] if "rewards/accuracies" in r]
    evaluations = [r for r in state["log_history"] if "eval_rewards/accuracies" in r]
    pick = lambda r, keys: {k: r[k] for k in keys}
    train_keys = ["step", "rewards/accuracies", "rewards/margins", "loss"]
    eval_keys = ["step", "eval_rewards/accuracies", "eval_rewards/margins", "eval_rewards/chosen", "eval_rewards/rejected", "eval_loss"]
    sft_state = json.loads(sources["sft_state"])
    counts = json.loads(sources["data_build"])
    return dict(
        schema_version=1,
        generation=generations,
        comparison=dict(ordered_ids_and_gold_match=True, paired_counts=dict(paired),
                        dpo_minus_sft_accuracy_percentage_points=100 * (generations["dpo"]["accuracy"] - generations["sft"]["accuracy"]),
                        dpo_minus_sft_mean_whitespace_words=generations["dpo"]["mean_whitespace_words"] - generations["sft"]["mean_whitespace_words"]),
        base_aligned_subset=dict(n=len(base["outputs"]), base_correct=generations["base40"]["correct"],
                                 dpo_correct=sum(dpo_by_id[r["id"]]["is_correct"] for r in base["outputs"]),
                                 scope="Descriptive subset check; different batch sizes and no locked historical runtime control"),
        preference=dict(global_step=state["global_step"], training_log_points=len(train),
                        first_training=pick(train[0], train_keys), last_training=pick(train[-1], train_keys),
                        evaluation_points=[pick(r, eval_keys) for r in evaluations]),
        sft_training=dict(global_step=sft_state["global_step"],
                          last_eval_loss=[r["eval_loss"] for r in sft_state["log_history"] if "eval_loss" in r][-1]),
        data_sizes={split: {k: counts[split][k] for k in ("input", "sft_rows", "strict_dpo_rows")} for split in ("train", "validation")},
        source_code_scope="Current frozen evaluator policy reproduces archived fields; does not prove historical code identity",
    )


def verify(bundle: Path, source_root: Path | None = None):
    manifest = json.loads((bundle / "manifest.json").read_text())
    require(manifest["schema_version"] == 1, "Unsupported manifest version")
    sources, descriptors = {}, {}
    bytes_checked = 0
    for item in manifest["sources"]:
        sid = item["id"]
        require(sid not in sources, "Duplicate source ID")
        snapshot = item["snapshot"]
        content = safe_path(bundle, snapshot["path"]).read_bytes()
        require(sha(content) == snapshot["sha256"], f"Snapshot hash mismatch: {sid}")
        require(len(content) == snapshot["bytes"], f"Snapshot size mismatch: {sid}")
        if snapshot["kind"] == "full":
            require(sha(content) == item["original_sha256"], f"Full source hash mismatch: {sid}")
        else:
            require(snapshot["kind"] == "line_excerpt", "Unknown snapshot type")
        if source_root:
            original = safe_path(source_root, item["source_path"]).read_bytes()
            require(sha(original) == item["original_sha256"], f"Original source changed: {sid}")
            if snapshot["kind"] == "full":
                expected = original
            else:
                expected = ("\n".join(original.decode("utf-8").splitlines()[snapshot["line_start"]-1:snapshot["line_end"]]) + "\n").encode()
            require(expected == content, f"Source excerpt mismatch: {sid}")
        sources[sid] = content.decode("utf-8")
        descriptors[sid] = item
        bytes_checked += len(content)
    artifacts = {}
    for name, descriptor in manifest["artifacts"].items():
        content = safe_path(bundle, descriptor["path"]).read_bytes()
        require(sha(content) == descriptor["sha256"], f"Artifact hash mismatch: {name}")
        artifacts[name] = json.loads(content)
    excerpts = {}
    for excerpt in artifacts["excerpts"]:
        require(excerpt["id"] not in excerpts, "Duplicate excerpt ID")
        descriptor = descriptors[excerpt["source_id"]]["snapshot"]
        start = descriptor.get("line_start", 1)
        lines = sources[excerpt["source_id"]].splitlines()
        a, b = excerpt["start_line"], excerpt["end_line"]
        require(start <= a <= b <= start + len(lines) - 1, "Excerpt line range is invalid")
        text = "\n".join(lines[a-start:b-start+1])
        require(text == excerpt["text"] and sha(text.encode()) == excerpt["sha256"], f"Excerpt content mismatch: {excerpt['id']}")
        excerpts[excerpt["id"]] = excerpt
    claim_ids = set()
    for claim in artifacts["claims"]:
        require(claim["id"] not in claim_ids, "Duplicate claim ID")
        claim_ids.add(claim["id"])
        require(claim["verdict"] in {"supported", "contradicted", "insufficient_evidence"}, "Unknown verdict")
        require(claim["evidence_ids"] and all(e in excerpts for e in claim["evidence_ids"]), "Claim has missing evidence references")
        require(claim["rationale_zh"] and claim["scope_zh"], "Claim lacks rationale/scope")
    observed = derive_observations(sources)
    require(observed == artifacts["observations"], "Derived observations differ from the saved record")
    return dict(status="passed", sources=len(sources), source_bytes=bytes_checked,
                excerpts=len(excerpts), claims_with_valid_references=len(claim_ids),
                rescored_generation_rows=sum(r["num_samples"] for r in observed["generation"].values()),
                original_source_files_checked=len(sources) if source_root else 0,
                verifier_sha256=sha(Path(__file__).read_bytes()),
                model_calls=0, training_runs=0, observations=observed,
                scope="Hash/excerpt/reference/arithmetic verification; semantic verdicts are analyst judgments, not automatically certified")


def self_test(bundle):
    tests = []
    for index, (text, expected) in enumerate([
            ("Answer: A\nWe had 60", "60"), ("Answer: 2 then Answer: 3", "3"),
            ("final answer is 1,200", "1200"), ("#### -2.50", "-2.5"), ("No digits", None)], 1):
        require(extract_answer(text) == expected, "Scoring policy regression")
        tests.append(f"scoring_policy_control_{index}")
    mutations = ["snapshot_bytes", "summary_after_rehash", "bad_excerpt_after_rehash", "missing_claim_reference_after_rehash", "path_escape"]
    for mutation in mutations:
        with tempfile.TemporaryDirectory(prefix="lablens-dpo-case-") as temp:
            copied = Path(temp) / "bundle"
            shutil.copytree(bundle, copied)
            manifest = json.loads((copied / "manifest.json").read_text())
            item = next(s for s in manifest["sources"] if s["id"] == "dpo_outputs")
            if mutation == "snapshot_bytes":
                with (copied / item["snapshot"]["path"]).open("ab") as stream:
                    stream.write(b" ")
            elif mutation == "summary_after_rehash":
                path = copied / item["snapshot"]["path"]
                data = json.loads(path.read_text()); data["summary"]["correct"] += 1
                content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(); path.write_bytes(content)
                item["snapshot"].update(sha256=sha(content), bytes=len(content)); item["original_sha256"] = sha(content)
            elif mutation in {"bad_excerpt_after_rehash", "missing_claim_reference_after_rehash"}:
                artifact = manifest["artifacts"]["excerpts" if mutation.startswith("bad_excerpt") else "claims"]
                path = copied / artifact["path"]; data = json.loads(path.read_text())
                if mutation.startswith("bad_excerpt"):
                    data[0]["text"] = "Altered source text"; data[0]["sha256"] = sha(data[0]["text"].encode())
                else:
                    data[0]["evidence_ids"] = ["missing-evidence"]
                content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(); path.write_bytes(content)
                artifact["sha256"] = sha(content)
            else:
                item["snapshot"]["path"] = "../outside.json"
            (copied / "manifest.json").write_text(json.dumps(manifest))
            try:
                verify(copied)
            except VerificationError:
                tests.append(mutation)
            else:
                raise VerificationError(f"Negative control was not rejected: {mutation}")
    return dict(passed=len(tests), total=len(tests), cases=tests,
                scope="Five numeric-parser examples and five negative integrity controls; no semantic model evaluation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--source-root", type=Path, help="Optional read-only original DPO checkout/hash comparison")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional verification JSON output")
    args = parser.parse_args()
    try:
        result = verify(args.bundle.resolve(), args.source_root.resolve() if args.source_root else None)
        if args.self_test:
            result["verifier_self_tests"] = self_test(args.bundle.resolve())
    except (VerificationError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
