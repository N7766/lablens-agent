#!/usr/bin/env python3
"""Prepare and execute a bounded, exposed Round 6 regression; never grade with keywords."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "docs/evaluations/conversation-round6"
OLD = REPO / "docs/evaluations/conversation-round5/development"
NEW = REPO / "docs/evaluations/conversation-round5/new-scenarios"
MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
CEILING = 70
spec = importlib.util.spec_from_file_location("round5_frozen_http_helpers", NEW / "frozen/runner.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
dump, sha, utc = helpers.dump, helpers.sha, helpers.utc


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_manifest(root, manifest):
    for relative, expected in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != expected:
            raise RuntimeError("Manifest file mismatch: " + relative)


def normalize():
    scenarios = []
    for scenario in read(OLD / "inputs/scenarios.json")["scenarios"]:
        sid = scenario["id"]
        documents = []
        source_root = OLD / scenario["workspace_relative"]
        for index, path in enumerate(sorted(source_root.rglob("*.md"))):
            relative = str(path.relative_to(source_root))
            documents.append({"id": f"{sid}-{index + 1}", "title": relative,
                              "text": path.read_text(), "source": f"synthetic:{sid}/{relative}"})
        scenarios.append({"id": sid, "group": "cedar-harbor", "documents": documents,
                          "conversation": {"title": "Round 6 exposed regression " + sid,
                                           **scenario["initial_state"]},
                          "turns": copy.deepcopy(scenario["turns"])})
    for original in read(NEW / "frozen/dataset.json")["scenarios"]:
        scenario = copy.deepcopy(original)
        scenario["group"] = "alder-kite"
        for turn in scenario["turns"]:
            turn["depends_on"] = [turn["depends_on"]] if turn["depends_on"] else []
        scenarios.append(scenario)
    turns = [t for s in scenarios for t in s["turns"]]
    if len(turns) != 10 or len({t["id"] for t in turns}) != 10:
        raise RuntimeError("Expected exactly ten distinct planned turns")
    return {"schema_version": 1, "scope": "All ten turns exposed; AI developer regression, not independent human evaluation",
            "scenarios": scenarios, "planned_turns": 10, "documents": 16}


def prepare(args):
    previous = BASE / "input-freeze.json"
    revision = getattr(args, "pre_run_revision", 1)
    supersedes = None
    if previous.exists():
        archive = BASE / "input-freeze-v1-pre-review.json"
        if (revision != 2 or not archive.is_file() or sha(previous) != sha(archive)
                or (BASE / "runs").exists()):
            raise RuntimeError("Input freeze already exists; only preserved, unexecuted v1 may become pre-run v2")
        supersedes = sha(archive)
    for root, name in ((OLD, "frozen-manifest.json"), (NEW, "freeze-manifest.json")):
        verify_manifest(root, read(root / name))
    dump(BASE / "frozen/dataset.json", normalize())
    paths = {Path(__file__).resolve(), BASE / "protocol.md", BASE / "frozen/dataset.json",
             REPO / "scripts/score_conversation_round6.py", REPO / "tests/test_round6_evaluation_harness.py"}
    if supersedes:
        paths.update((BASE / "input-freeze-v1-pre-review.json", BASE / "pre-run-revision.md"))
        paths.update((BASE / "pre-review-v1").glob("*.py.txt"))
    for root, name in ((OLD, "frozen-manifest.json"), (NEW, "freeze-manifest.json")):
        paths.add(root / name)
        paths.update(root / p for p in read(root / name)["files"])
    manifest = {"schema_version": 1, "input_revision": revision, "supersedes_pre_run_sha256": supersedes,
                "frozen_at": utc(), "scope": "ten exposed AI synthetic regression turns",
                "planned_turns": 10, "max_model_attempts": CEILING,
                "original_gold_unchanged": True,
                "files": {str(p.relative_to(REPO)): sha(p) for p in sorted(paths)}}
    dump(BASE / "input-freeze.json", manifest)
    print(json.dumps({"input_freeze": str(BASE / "input-freeze.json"),
                      "sha256": sha(BASE / "input-freeze.json"), "file_count": len(manifest["files"])}))


def verify_source(approval):
    if not re.fullmatch(r"[0-9a-f]{40}", approval.get("candidate_sha", "")):
        raise RuntimeError("Full candidate Git SHA required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    if head != approval["candidate_sha"]:
        raise RuntimeError("HEAD changed since candidate approval")
    manifest = approval.get("source_files")
    if not isinstance(manifest, dict) or not manifest:
        raise RuntimeError("Root-reviewed source/config hash map is required")
    verify_manifest(REPO, {"files": manifest})
    return {"checked_at": utc(), "candidate_sha": head, "source_files": manifest, "match": True}


def verify_approved_inputs(approval):
    approved_sha = approval.get("input_freeze_sha256")
    if not isinstance(approved_sha, str) or approved_sha != sha(BASE / "input-freeze.json"):
        raise RuntimeError("Approved input freeze identity changed")
    manifest = read(BASE / "input-freeze.json")
    verify_manifest(REPO, manifest)
    return {"checked_at": utc(), "input_freeze_sha256": approved_sha,
            "file_count": len(manifest["files"]), "match": True}


def verify_approval(approval, base):
    expected = {"approved_for_once_only_inference": True, "model": MODEL,
                "max_decision_calls_per_task": 6, "max_review_calls_per_task": 1,
                "max_model_calls_per_task": 7, "max_model_calls_total": CEILING,
                "service_boundary_seconds": 75, "provider_retry_enabled": False,
                "task_retry_enabled": False, "embedding_enabled": False,
                "max_output_unchanged": True, "isolated_qa": True}
    for key, value in expected.items():
        if approval.get(key) != value:
            raise RuntimeError("Missing/mismatched root attestation: " + key)
    if approval.get("endpoint", "").rstrip("/") != base.rstrip("/"):
        raise RuntimeError("Approved QA endpoint differs")
    verify_approved_inputs(approval)
    if not approval.get("configuration_evidence") or not approval.get("max_output_configuration"):
        raise RuntimeError("Need budget/output configuration values and evidence locations")
    return verify_source(approval)


def usage_flags(task):
    usage, origin = helpers.usage_from(task)
    tokens = usage.get("generation_tokens") if isinstance(usage, dict) else None
    calls_known = (isinstance(usage, dict) and type(usage.get("model_calls")) is int
                   and usage["model_calls"] >= 0)
    tokens_unknown = (not isinstance(usage, dict) or bool(task.get("usage_unknown"))
                      or usage.get("provider_usage_unknown") is not False
                      or not isinstance(tokens, dict)
                      or any(type(tokens.get(k)) is not int or tokens[k] < 0 for k in
                             ("prompt_tokens", "completion_tokens", "total_tokens"))
                      or trace_usage_unknown(task, usage.get("model_calls") if isinstance(usage, dict) else None))
    return usage, origin, calls_known, tokens_unknown


def trace_usage_unknown(task, expected_calls):
    """A complete aggregate cannot erase an uncertain or unclosed provider request."""
    result = task.get("result") or task.get("error_result") or {}
    trace = (result.get("agent") or {}).get("trace")
    if not isinstance(trace, list):
        return True
    started = [e for e in trace if e.get("event") == "request_started"]
    finished = [e for e in trace if e.get("event") == "request_finished"]
    if len(started) != expected_calls or len(finished) != len(started):
        return True
    ids = [e.get("request_id") for e in started]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        return True
    for begin in started:
        ends = [e for e in finished if e.get("request_id") == begin["request_id"]]
        if len(ends) != 1:
            return True
        end = ends[0]
        if (end.get("phase") != begin.get("phase") or end.get("usage_known") is not True
                or end.get("provider_acceptance") != "response_received"
                or end.get("response_received") is not True):
            return True
        tokens = end.get("usage")
        if (not isinstance(tokens, dict) or any(type(tokens.get(k)) is not int or tokens[k] < 0
                                               for k in ("prompt_tokens", "completion_tokens", "total_tokens"))):
            return True
    return False


def audit_trace_attempts(task, expected):
    result = task.get("result") or task.get("error_result") or {}
    trace = (result.get("agent") or {}).get("trace")
    if not isinstance(trace, list):
        raise RuntimeError("Request lifecycle trace unavailable")
    started = [e for e in trace if e.get("event") == "request_started"]
    counts = {phase: sum(e.get("phase") == phase for e in started) for phase in ("decision", "review")}
    if len(started) != expected or sum(counts.values()) != expected:
        raise RuntimeError("Trace attempts disagree with execution usage")
    if counts["decision"] > 6 or counts["review"] > 1:
        raise RuntimeError("Per-phase provider attempt ceiling exceeded")
    return counts


def run(args):
    approval = read(args.approval_file)
    first_identity = verify_approval(approval, args.base_url)
    approved_input_sha = approval["input_freeze_sha256"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", args.run_label):
        raise RuntimeError("Safe fresh run label required")
    data = read(BASE / "frozen/dataset.json")
    output = BASE / "runs" / args.run_label
    output.mkdir(parents=True, exist_ok=False)
    (output / "raw").mkdir()
    api = helpers.API(args.base_url, output)
    dump(output / "approval.json", approval)
    dump(output / "source-before.json", first_identity)
    outcomes = {t["id"]: {"turn_id": t["id"], "scenario_id": s["id"], "group": s["group"],
                          "depends_on": t["depends_on"], "status": "not_submitted", "task_id": None}
                for s in data["scenarios"] for t in s["turns"]}
    submitted, known_calls, fatal, active = 0, 0, None, None
    unknown_calls, unknown_tokens = [], []
    try:
        dump(output / "system-before.json", api.request("GET", "/api/system/status"))
        for scenario in data["scenarios"]:
            sid = scenario["id"]
            workspace = api.request("POST", "/api/workspaces", {"name": "round6-" + sid + "-" + args.run_label})
            wid = workspace["id"]
            wp = "/api/workspaces/" + helpers.path_id(wid)
            dump(output / (sid + "-workspace.json"), workspace)
            for document in scenario["documents"]:
                api.request("PUT", wp + "/documents/" + helpers.path_id(document["id"]),
                            {k: document[k] for k in ("title", "text", "source")})
            dump(output / (sid + "-documents.json"), api.request("GET", wp + "/documents"))
            conversation = api.request("POST", wp + "/conversations", scenario["conversation"])
            cp = wp + "/conversations/" + helpers.path_id(conversation["id"])
            dump(output / (sid + "-conversation-created.json"), conversation)
            for turn in scenario["turns"]:
                current = outcomes[turn["id"]]
                dump(output / (turn["id"] + "-inputs-before.json"), verify_approved_inputs(approval))
                current.update({"workspace_id": wid, "conversation_id": conversation["id"]})
                conversation = api.request("GET", cp)
                blocked = [dep for dep in turn["depends_on"] if outcomes[dep]["status"] != "SUCCEEDED"
                           or not helpers.valid_previous(conversation, outcomes[dep]["task_id"])]
                if blocked:
                    current.update({"status": "blocked_no_valid_prior_response", "blocked_by": blocked})
                    dump(output / "outcomes-checkpoint.json", outcomes)
                    continue
                if submitted >= 10 or known_calls > CEILING - 7:
                    raise RuntimeError("Pre-submission attempt ceiling")
                dump(output / (turn["id"] + "-source-before.json"), verify_source(approval))
                dump(output / (turn["id"] + "-system-before.json"), api.request("GET", "/api/system/status"))
                documents = api.request("GET", wp + "/documents")
                payload = {"question": turn["question"], "strategy": "bm25", "mode": "agent", "top_k": 5,
                           "approved_revision": documents["revision"],
                           "expected_conversation_revision": conversation["revision"]}
                if "state_update" in turn:
                    payload["state"] = turn["state_update"]
                key = "r6-" + turn["id"] + "-" + uuid.uuid4().hex
                current.update({"status": "submission_attempted", "idempotency_key": key, "request": payload})
                submitted += 1
                dump(output / "outcomes-checkpoint.json", outcomes)
                try:
                    task = api.request("POST", cp + "/turns", payload, key=key)
                except BaseException:
                    current["status"] = "uncertain_submission_or_http_failure"
                    raise
                task_id = task["id"]
                current.update({"task_id": task_id, "status": task.get("status", "unknown")})
                task_path = wp + "/tasks/" + helpers.path_id(task_id)
                active = task_path
                dump(output / (turn["id"] + "-submission.json"), task)
                print(turn["id"] + ": submitted " + task_id, flush=True)
                started = time.monotonic()
                deadline = started + 200
                while task.get("status") not in helpers.TERMINAL:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        current["status"] = "client_observation_timeout"
                        # Clear before calling: even a failed cancellation must not be retried.
                        active = None
                        dump(output / (turn["id"] + "-cancel.json"), api.request("POST", task_path + "/cancel"))
                        raise RuntimeError("200-second observation deadline; later submissions stopped")
                    time.sleep(min(2, remaining))
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        task = api.request("GET", task_path, timeout=min(20, remaining))
                active = None
                current.update({"status": task["status"], "observation_seconds": time.monotonic() - started,
                                "task_file": turn["id"] + "-final-task.json", "error_code": task.get("error_code")})
                dump(output / current["task_file"], task)
                conversation = api.request("GET", cp)
                dump(output / (turn["id"] + "-conversation.json"), conversation)
                current["valid_saved_response"] = helpers.valid_previous(conversation, task_id)
                usage, origin, calls_known, tokens_unknown = usage_flags(task)
                current.update({"execution_usage": usage, "usage_origin": origin,
                                "task_usage_unknown": task.get("usage_unknown"),
                                "tokens_or_provider_unknown": tokens_unknown})
                if tokens_unknown:
                    unknown_tokens.append(turn["id"])
                if not calls_known:
                    unknown_calls.append(turn["id"])
                    raise RuntimeError("Attempt count unknown; later submissions stopped")
                known_calls += usage["model_calls"]
                if usage["model_calls"] > 7 or known_calls > CEILING:
                    raise RuntimeError("Reported model attempt ceiling exceeded")
                if any(usage.get(k) not in (0, None) for k in ("embedding_requests", "embedded_inputs")):
                    raise RuntimeError("Unexpected embedding activity")
                if usage.get("cache_reused") is True:
                    raise RuntimeError("Task cache reused; cannot claim fresh inference")
                current["trace_attempts"] = audit_trace_attempts(task, usage["model_calls"])
                dump(output / "outcomes-checkpoint.json", outcomes)
                print(f"{turn['id']}: {task['status']}; attempts={usage['model_calls']}; tokens_unknown={tokens_unknown}", flush=True)
        dump(output / "system-after.json", api.request("GET", "/api/system/status"))
    except BaseException as error:
        fatal = {"type": type(error).__name__, "message": str(error), "at": utc()}
        if active:
            try:
                dump(output / "interrupt-cancel.json", api.request("POST", active + "/cancel"))
            except BaseException as cancel_error:
                fatal["cancel_error"] = {"type": type(cancel_error).__name__, "message": str(cancel_error)}
        for current in outcomes.values():
            if current["status"] == "not_submitted":
                invalid = any(outcomes[dep]["status"] != "SUCCEEDED" or not outcomes[dep].get("valid_saved_response")
                              for dep in current["depends_on"])
                current["status"] = "blocked_no_valid_prior_response" if invalid else "blocked_run_stopped"
    finally:
        try:
            dump(output / "inputs-after.json", verify_approved_inputs(approval))
        except BaseException as input_error:
            dump(output / "inputs-after-error.json", {"type": type(input_error).__name__, "message": str(input_error)})
            fatal = fatal or {"type": type(input_error).__name__, "message": str(input_error), "at": utc()}
        try:
            dump(output / "source-after.json", verify_source(approval))
        except BaseException as source_error:
            dump(output / "source-after-error.json", {"type": type(source_error).__name__, "message": str(source_error)})
            fatal = fatal or {"type": type(source_error).__name__, "message": str(source_error), "at": utc()}
        for tid, current in outcomes.items():
            if current.get("idempotency_key") and not isinstance(current.get("execution_usage"), dict):
                if tid not in unknown_calls:
                    unknown_calls.append(tid)
                if tid not in unknown_tokens:
                    unknown_tokens.append(tid)
        summary = {"schema_version": 1, "completed_at": utc(), "candidate_sha": approval["candidate_sha"],
                   "scope": "ten exposed AI synthetic regression turns; not blind or human-independent",
                   "input_freeze_sha256": approved_input_sha, "planned_turns": 10,
                   "task_submission_attempts": submitted, "known_model_calls": known_calls,
                   "unknown_model_call_tasks": unknown_calls, "unknown_token_or_provider_tasks": unknown_tokens,
                   "max_model_calls": CEILING, "fatal": fatal, "turns": list(outcomes.values()),
                   "semantic_reading_started": False}
        dump(output / "run-summary.json", summary)
        raw_sha = helpers.seal(output)
        print(json.dumps({"raw_seal_sha256": raw_sha, "run_directory": str(output),
                          "statuses": {k: v["status"] for k, v in outcomes.items()},
                          "attempts": known_calls, "unknown_token_or_provider_tasks": unknown_tokens,
                          "fatal": fatal}, ensure_ascii=False, indent=2))
    return 1 if fatal else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preparing = sub.add_parser("prepare")
    preparing.add_argument("--pre-run-revision", type=int, choices=(1, 2), default=1)
    running = sub.add_parser("run")
    running.add_argument("--go", required=True, choices=("GO",))
    running.add_argument("--approval-file", required=True, type=Path)
    running.add_argument("--base-url", required=True)
    running.add_argument("--run-label", required=True)
    args = parser.parse_args()
    return prepare(args) if args.command == "prepare" else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
