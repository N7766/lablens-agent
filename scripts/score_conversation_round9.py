#!/usr/bin/env python3
"""Validate seals and aggregate explicit AI annotations; this does not infer semantic grades."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("round9_runner", REPO / "scripts/evaluate_conversation_round9.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
BASE, OLD, NEW, read, dump, sha = runner.BASE, runner.OLD, runner.NEW, runner.read, runner.dump, runner.sha
AXES = ("semantic_completeness", "citation_entailment", "retrieval_coverage", "false_refusal", "referent_resolution", "source_scope")
DIMS = ("retrieval_coverage", "conclusion_correctness", "completeness", "citation_entailment", "erroneous_refusal", "reference_scope")
DIAGNOSTICS = {
    "core_conclusion": ("correct", "partly_correct", "incorrect", "not_evaluable"),
    "completeness_gap": ("none", "explicit_question_omission", "gold_specific_detail", "mixed", "not_evaluable"),
    "erroneous_refusal": ("none", "partial", "blanket", "not_evaluable"),
    "evidence_scope": ("preserved", "ambiguous", "unsupported_inference", "not_evaluable"),
    "missing_information_handling": ("bounded_answer", "overclaimed", "unnecessary_clarification", "not_applicable", "not_evaluable"),
    "contradiction_handling": ("reconciled", "overclaimed", "omitted", "not_applicable", "not_evaluable"),
    "erroneous_clarification": ("none", "partial", "blanket", "not_evaluable"),
}
DELIVERY_TYPES = ("fact_report", "clarification", "mixed", "no_response")


def need(value, message):
    if not value:
        raise ValueError(message)


def verified_run(run):
    runner.verify_manifest(REPO, read(BASE / "input-freeze.json"))
    runner.verify_manifest(run, read(run / "raw-seal.json"))
    summary = read(run / "run-summary.json")
    need(summary["input_freeze_sha256"] == sha(BASE / "input-freeze.json"), "Run/input identity mismatch")
    expected = {t["id"] for s in read(BASE / "frozen/dataset.json")["scenarios"] for t in s["turns"]}
    need(len(summary["turns"]) == 10 and {t["turn_id"] for t in summary["turns"]} == expected,
         "Run must retain exactly ten planned turn identities")
    verify_execution_identity(run, summary)
    return summary


def verify_execution_identity(run, summary):
    """A sealed failed source/input check is evidence of an invalid collection."""
    files = read(run / "raw-seal.json")["files"]
    need(not {"source-after-error.json", "inputs-after-error.json"}.intersection(files),
         "Execution source/input identity failed; formal scoring is prohibited")
    required = {"run-summary.json", "approval.json", "source-before.json", "source-after.json", "inputs-after.json"}
    need(required.issubset(files), "Sealed source/input identity evidence is incomplete")
    approval = read(run / "approval.json")
    source_files = approval.get("source_files")
    need(isinstance(source_files, dict) and source_files, "Approved source map is missing")
    need(approval.get("candidate_sha") == summary["candidate_sha"]
         and approval.get("input_freeze_sha256") == summary["input_freeze_sha256"], "Approved run identity mismatch")
    for path in ("source-before.json", "source-after.json"):
        identity = read(run / path)
        need(identity.get("match") is True and identity.get("candidate_sha") == summary["candidate_sha"]
             and identity.get("source_files") == source_files, "Execution source identity mismatch")
    inputs = read(run / "inputs-after.json")
    need(inputs.get("match") is True and inputs.get("input_freeze_sha256") == summary["input_freeze_sha256"],
         "Execution input identity mismatch")
    fatal = summary.get("fatal")
    # Detect a mid-run failed identity check even if a file was restored before
    # finally and the last check consequently passed. Preserve ordinary partial
    # infrastructure failures, whose sealed identity can still be sound.
    if isinstance(fatal, dict):
        message = fatal.get("message", "")
        need(not any(marker in message for marker in ("Manifest file mismatch", "HEAD changed",
                 "Approved input freeze identity changed", "Full candidate Git SHA required",
                 "Root-reviewed source/config hash map is required")),
             "Execution source/input identity failed during collection; formal scoring is prohibited")


def template(args):
    summary = verified_run(args.run)
    old_gold = read(OLD / "gold/rubric.json")["turns"]
    new_gold = read(NEW / "frozen/gold.json")["turns"]
    rows = []
    for outcome in summary["turns"]:
        tid = outcome["turn_id"]
        completed = outcome["status"] == "SUCCEEDED" and outcome.get("valid_saved_response") is True
        gold = old_gold[tid] if tid in old_gold else new_gold[tid]
        row = {"turn_id": tid, "group": outcome["group"],
               "evaluation_status": "evaluated" if completed else "not_evaluable", "summary": "",
               "delivery": {"type": "" if completed else "no_response", "rationale": "", "evidence_pointers": []},
               "diagnostics": {k: {"label": "" if completed else "not_evaluable", "rationale": "", "evidence_pointers": []}
                               for k in DIAGNOSTICS},
               "retrieval_review": [], "citation_review": [], "limitations": []}
        if tid in old_gold:
            row["axes"] = {k: {"status": "", "rationale": "", "evidence_pointers": []} for k in AXES}
            row["mandatory_checks"] = [{"index": i, "requirement": text, "status": "", "rationale": "", "evidence_pointers": []}
                                       for i, text in enumerate(gold["mandatory"])]
            row["forbidden_checks"] = [{"index": i, "claim": text, "present": None, "rationale": "", "evidence_pointers": []}
                                       for i, text in enumerate(gold["forbidden"])]
        else:
            row["dimensions"] = {k: {"score": None, "rationale": "", "evidence_pointers": []} for k in DIMS}
            row["claim_checks"] = [{"gold_claim_id": c["id"], "claim": c["claim"], "verdict": "", "rationale": "", "evidence_pointers": []}
                                   for c in gold["required_conclusions"]]
        rows.append(row)
    annotation = {"schema_version": 1, "scope": "AI developer grading of exposed synthetic regression; not blind/human-independent",
                  "candidate_sha": summary["candidate_sha"], "input_freeze_sha256": sha(BASE / "input-freeze.json"),
                  "raw_seal_sha256": sha(args.run / "raw-seal.json"), "turns": rows}
    need(not args.output.exists(), "Refuse to overwrite annotations")
    dump(args.output, annotation)
    print(str(args.output))


class EvidenceResolver:
    """Resolve explicit raw:/input: references, including JSON-pointer or line fragments."""

    def __init__(self, run):
        self.collections = {"raw": (run.resolve(), read(run / "raw-seal.json")["files"]),
                            "input": (REPO.resolve(), read(BASE / "input-freeze.json")["files"])}

    def validate(self, pointer):
        need(isinstance(pointer, str) and pointer and pointer == pointer.strip(), "Evidence pointer must be a nonempty string")
        prefix, separator, location = pointer.partition(":")
        need(separator and prefix in self.collections, "Evidence pointer requires raw: or input: collection")
        relative, fragment_sep, fragment = location.partition("#")
        root, files = self.collections[prefix]
        need(relative and not Path(relative).is_absolute() and relative in files
             and all(part not in ("", ".", "..") for part in relative.split("/")),
             "Evidence target is outside the sealed collection: " + relative)
        path = (root / relative).resolve()
        need(path.is_relative_to(root) and path.is_file() and sha(path) == files[relative],
             "Evidence file missing, outside root, or changed: " + relative)
        if not fragment_sep:
            return
        need(fragment, "Empty evidence fragment")
        if fragment.startswith("/"):
            try:
                node = read(path)
            except (UnicodeError, ValueError):
                raise ValueError("JSON pointer requires a JSON target") from None
            for encoded in fragment[1:].split("/"):
                need(re.search(r"~(?![01])", encoded) is None, "Invalid JSON pointer escape")
                key = encoded.replace("~1", "/").replace("~0", "~")
                if isinstance(node, list):
                    need(re.fullmatch(r"0|[1-9][0-9]*", key) is not None and int(key) < len(node),
                         "JSON pointer array index out of range")
                    node = node[int(key)]
                else:
                    need(isinstance(node, dict) and key in node, "JSON pointer target does not exist")
                    node = node[key]
            need(node is not None, "Evidence pointer resolves to null; cite its containing record instead")
        else:
            match = re.fullmatch(r"L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?", fragment)
            need(match, "Fragment must be a JSON pointer or Lstart-Lend")
            start, end = int(match[1]), int(match[2] or match[1])
            need(start <= end <= len(path.read_text().splitlines()), "Evidence line range out of bounds")


def prose(record, label, resolver):
    need(isinstance(record, dict), label + " requires a structured review record")
    need(isinstance(record.get("rationale"), str) and record["rationale"].strip(), label + " needs rationale")
    need(isinstance(record.get("evidence_pointers"), list) and record["evidence_pointers"], label + " needs evidence pointers")
    for pointer in record["evidence_pointers"]:
        resolver.validate(pointer)


def validate_delivery(record, delivered, resolver):
    prose(record, "delivery", resolver)
    need(record.get("type") in DELIVERY_TYPES, "Invalid explicit delivery type")
    need((record["type"] != "no_response") == delivered, "Delivery type conflicts with persisted response")
    return record["type"]


def validate_strict_consistency(annotation, delivery, clarification_allowed):
    """Reject contradictory annotation records; never rewrite the original gold."""
    need(delivery != "clarification" or clarification_allowed,
         "Strict answer pass conflicts with pure clarification delivery")
    compatible = {"core_conclusion": {"correct"}, "completeness_gap": {"none"},
                  "erroneous_refusal": {"none"}, "evidence_scope": {"preserved"},
                  "missing_information_handling": {"bounded_answer", "not_applicable"},
                  "contradiction_handling": {"reconciled", "not_applicable"},
                  "erroneous_clarification": {"none"}}
    for dimension, allowed in compatible.items():
        need(annotation["diagnostics"][dimension]["label"] in allowed,
             "Strict pass conflicts with diagnostic annotation: " + dimension)


def score(args):
    summary = verified_run(args.run)
    resolver = EvidenceResolver(args.run)
    annotations = read(args.annotations)
    for field, expected in (("candidate_sha", summary["candidate_sha"]),
                            ("input_freeze_sha256", sha(BASE / "input-freeze.json")),
                            ("raw_seal_sha256", sha(args.run / "raw-seal.json"))):
        need(annotations.get(field) == expected, "Annotation identity mismatch: " + field)
    outcomes = {t["turn_id"]: t for t in summary["turns"]}
    need(len(annotations["turns"]) == 10 and {t["turn_id"] for t in annotations["turns"]} == set(outcomes),
         "Annotations must retain each planned turn exactly once")
    old_gold, new_gold = read(OLD / "gold/rubric.json")["turns"], read(NEW / "frozen/gold.json")["turns"]
    groups = {"cedar-harbor": {"planned": 6, "delivered": 0, "evaluated": 0, "strict_pass_turns": [], "axes_full_counts": {k: 0 for k in AXES}},
              "alder-kite": {"planned": 4, "delivered": 0, "evaluated": 0, "strict_pass_turns": [], "axes_full_counts": {k: 0 for k in DIMS}}}
    for group in groups.values():
        group["delivery_types"] = dict.fromkeys(DELIVERY_TYPES, 0)
    rows = []
    for annotation in annotations["turns"]:
        tid = annotation["turn_id"]
        outcome = outcomes[tid]
        need(annotation["group"] == outcome["group"], tid + " group mismatch")
        group = groups[outcome["group"]]
        delivered = outcome["status"] == "SUCCEEDED" and outcome.get("valid_saved_response") is True
        evaluated = annotation["evaluation_status"] == "evaluated"
        need(annotation["evaluation_status"] in ("evaluated", "not_evaluable") and evaluated == delivered,
             tid + " must evaluate all delivered saved responses and no other turns")
        group["delivered"] += int(delivered)
        group["evaluated"] += int(evaluated)
        delivery = validate_delivery(annotation.get("delivery"), delivered, resolver)
        group["delivery_types"][delivery] += 1
        need(annotation.get("summary"), tid + " needs summary")
        need(set(annotation["diagnostics"]) == set(DIAGNOSTICS), tid + " diagnostic schema mismatch")
        for dimension, allowed in DIAGNOSTICS.items():
            record = annotation["diagnostics"][dimension]
            need(record["label"] in allowed, tid + " invalid diagnostic label")
            need((record["label"] == "not_evaluable") == (not evaluated), tid + " diagnostic evaluability mismatch")
            prose(record, tid + "/" + dimension, resolver)
        if not evaluated:
            need(annotation.get("limitations"), tid + " must state non-evaluability reason")
        if tid in old_gold:
            gold = old_gold[tid]
            need(set(annotation["axes"]) == set(AXES), tid + " missing old axis")
            for axis, record in annotation["axes"].items():
                need(record["status"] in ("pass", "fail", "blocked", "not_applicable"), tid + " invalid old axis")
                prose(record, tid + "/" + axis, resolver)
                if not evaluated:
                    expected = "blocked" if axis in ("citation_entailment", "retrieval_coverage") else "fail"
                    need(record["status"] == expected, tid + " failed execution must follow original rubric")
                elif record["status"] == "not_applicable":
                    need(not gold["retrieval_applicable"] and axis in ("citation_entailment", "retrieval_coverage"),
                         tid + " not_applicable limited to pure clarification evidence axes")
                group["axes_full_counts"][axis] += int(record["status"] == "pass")
            checks = annotation["mandatory_checks"]
            need(len(checks) == len(gold["mandatory"]) and {c["index"] for c in checks} == set(range(len(checks))), tid + " missing mandatory check")
            for check in checks:
                need(check["requirement"] == gold["mandatory"][check["index"]], tid + " gold text changed")
                need(check["status"] in ("pass", "fail", "blocked"), tid + " mandatory status invalid")
                prose(check, tid + "/mandatory", resolver)
            forbidden = annotation["forbidden_checks"]
            need(len(forbidden) == len(gold["forbidden"]) and {c["index"] for c in forbidden} == set(range(len(forbidden))), tid + " missing forbidden check")
            for check in forbidden:
                need(check["claim"] == gold["forbidden"][check["index"]], tid + " forbidden text changed")
                need(type(check["present"]) is bool if evaluated else check["present"] is None, tid + " forbidden presence invalid")
                prose(check, tid + "/forbidden", resolver)
            passed = evaluated and all(r["status"] in ("pass", "not_applicable") for r in annotation["axes"].values())
            if passed:
                need(all(c["status"] == "pass" for c in checks) and all(c["present"] is False for c in forbidden),
                     tid + " strict pass conflicts with gold checks")
            original_dimensions = {k: v["status"] for k, v in annotation["axes"].items()}
        else:
            gold = new_gold[tid]
            need(set(annotation["dimensions"]) == set(DIMS), tid + " missing new dimension")
            for dimension, record in annotation["dimensions"].items():
                valid = (type(record["score"]) is int and record["score"] in (0, 1, 2)
                         if evaluated else record["score"] is None)
                need(valid, tid + " invalid score: delivered responses require every dimension")
                prose(record, tid + "/" + dimension, resolver)
                group["axes_full_counts"][dimension] += int(record["score"] == 2)
            checks = annotation["claim_checks"]
            expected = {c["id"]: c for c in gold["required_conclusions"]}
            need(len(checks) == len(expected) and {c["gold_claim_id"] for c in checks} == set(expected), tid + " missing gold claim")
            for check in checks:
                need(check["claim"] == expected[check["gold_claim_id"]]["claim"], tid + " gold claim changed")
                need(check["verdict"] in (("satisfied", "partial", "missing", "contradicted") if evaluated else ("not_evaluable",)), tid + " claim verdict invalid")
                prose(check, tid + "/gold claim", resolver)
            passed = evaluated and all(r["score"] == 2 for r in annotation["dimensions"].values())
            if passed:
                need(all(c["verdict"] == "satisfied" for c in checks), tid + " strict pass conflicts with gold claims")
            original_dimensions = {k: v["score"] for k, v in annotation["dimensions"].items()}
        if evaluated:
            need(annotation["retrieval_review"] and annotation["citation_review"], tid + " must review retrieval and citation entailment or explain pure clarification")
        for review_type in ("retrieval_review", "citation_review"):
            need(isinstance(annotation[review_type], list), tid + " review must be a list")
            for review in annotation[review_type]:
                prose(review, tid + "/" + review_type, resolver)
        if passed:
            validate_strict_consistency(annotation, delivery,
                                        tid in old_gold and not old_gold[tid]["retrieval_applicable"])
            group["strict_pass_turns"].append(tid)
        rows.append({"turn_id": tid, "execution_status": outcome["status"], "evaluated": evaluated,
                     "delivery_type": delivery,
                     "strict_pass": passed, "original_dimensions": original_dimensions,
                     "diagnostics": annotation["diagnostics"]})
    for group in groups.values():
        count = len(group["strict_pass_turns"])
        group["strict_count"] = count
        group["strict_all_planned"] = f"{count}/{group['planned']}"
        group["strict_evaluated"] = f"{count}/{group['evaluated']}"
    result = {"schema_version": 1, "candidate_sha": summary["candidate_sha"],
              "scope": "exposed synthetic developer regression; distinct original gold rubrics, no pooled accuracy",
              "input_freeze_sha256": sha(BASE / "input-freeze.json"), "raw_seal_sha256": sha(args.run / "raw-seal.json"),
              "annotations_sha256": sha(args.annotations), "groups": groups,
              "delivered_all_planned": f"{sum(g['delivered'] for g in groups.values())}/10",
              "known_model_calls": summary["known_model_calls"],
              "unknown_model_call_tasks": summary["unknown_model_call_tasks"],
              "unknown_token_or_provider_tasks": summary["unknown_token_or_provider_tasks"],
              "execution_fatal": summary.get("fatal"),
              "execution_validity": {"source_input_identity_valid": True,
                  "collection_complete": summary.get("fatal") is None,
                  "status": "valid_partial_collection" if summary.get("fatal") else "valid_completed_collection"},
              "turns": rows}
    need(not args.output.exists(), "Refuse to overwrite an existing score; use a separate review/version")
    dump(args.output, result)
    print(json.dumps({"groups": groups, "delivered_all_planned": result["delivered_all_planned"]}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("template", "score"):
        child = sub.add_parser(command)
        child.add_argument("--run", required=True, type=Path)
        child.add_argument("--output", required=True, type=Path)
        if command == "score":
            child.add_argument("--annotations", required=True, type=Path)
    args = parser.parse_args()
    template(args) if args.command == "template" else score(args)


if __name__ == "__main__":
    main()
