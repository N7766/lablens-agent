#!/usr/bin/env python3
"""Run frozen, newly authored synthetic conversations through the public JVM API.

This is a local AI evaluation harness, not a human or production evaluation.
It deliberately never retries task submission and never invents missing history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


TERMINAL = {"SUCCEEDED", "FAILED"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class Recorder:
    def __init__(self, endpoint: str, root: Path):
        self.endpoint = endpoint.rstrip("/")
        self.root = root
        self.counter = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, payload: object = None,
             key: str | None = None) -> object:
        self.counter += 1
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode()
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(self.endpoint + path, data=data,
                                         headers=headers, method=method)
        started = time.time()
        record = {"sequence": self.counter, "method": method, "path": path,
                  "payload": payload, "idempotency_key": key,
                  "started_unix": started}
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read().decode()
                record.update(status=response.status, response_headers=dict(response.headers),
                              raw_response=body, elapsed_seconds=time.time() - started)
        except urllib.error.HTTPError as error:
            record.update(status=error.code, raw_response=error.read().decode(),
                          elapsed_seconds=time.time() - started)
            save(self.root / "http" / f"{self.counter:04d}.json", record)
            raise RuntimeError(f"HTTP {error.code}: {method} {path}") from error
        except Exception as error:
            record.update(transport_error=type(error).__name__,
                          elapsed_seconds=time.time() - started)
            save(self.root / "http" / f"{self.counter:04d}.json", record)
            raise
        save(self.root / "http" / f"{self.counter:04d}.json", record)
        return json.loads(body) if body else None


def observed_fingerprints(value: object, path: str = "") -> list[dict]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}/{key}"
            if any(word in key.lower() for word in ("fingerprint", "sha256", "snapshot_id")):
                found.append({"json_path": item_path, "value": item})
            if key.lower() in {"source", "document_id", "source_id", "passage_id"}:
                found.append({"json_path": item_path, "value": item})
            found.extend(observed_fingerprints(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(observed_fingerprints(item, f"{path}/{index}"))
    return found


def run(args: argparse.Namespace) -> None:
    private = args.private.resolve()
    manifest = json.loads((private / "frozen-manifest.json").read_text())
    expected_manifest = (private / "frozen-manifest.sha256").read_text().split()[0]
    if digest(private / "frozen-manifest.json") != expected_manifest:
        raise RuntimeError("Frozen manifest hash mismatch")
    for relative, expected in manifest["files"].items():
        if digest(private / relative) != expected:
            raise RuntimeError(f"Frozen input changed: {relative}")
    data = json.loads((private / "inputs/scenarios.json").read_text())
    turns = [turn for scenario in data["scenarios"] for turn in scenario["turns"]]
    if len(turns) > 6:
        raise RuntimeError("Six-task ceiling exceeded")
    output = private / "run"
    if output.exists():
        raise RuntimeError("Run output already exists; no automatic rerun is permitted")
    output.mkdir()
    api = Recorder(args.endpoint, output)
    development = args.evaluation_label == "development-regression"
    evaluation_scope = ("Development regression on previously exposed AI-authored synthetic cases; "
                        "not blind, human-independent, or production evaluation" if development else
                        "AI-authored synthetic local evaluation; not human or production")
    summary = {"scope": evaluation_scope, "evaluation_label": args.evaluation_label,
               "input_manifest_sha256": expected_manifest, "endpoint": args.endpoint,
               "max_agent_tasks": 6, "max_model_calls_default_bound": 42,
               "task_submission_attempts": 0, "started_unix": time.time(),
               "scenarios": [], "turns": []}
    save(output / "runner-metadata.json", {"script_sha256": digest(Path(__file__)),
                                          "evaluation_label": args.evaluation_label,
                                          "input_manifest_sha256": expected_manifest})
    try:
        save(output / "system-before.json", api.call("GET", "/api/system/status"))
        for scenario in data["scenarios"]:
            sid = scenario["id"]
            workspace = api.call("POST", "/api/workspaces", {
                "name": f"round1-{args.evaluation_label}-{sid}-{uuid.uuid4().hex[:8]}"})
            wid = workspace["id"]
            document_map = []
            source_root = private / scenario["workspace_relative"]
            for index, source in enumerate(sorted(source_root.rglob("*.md"))):
                relative = str(source.relative_to(source_root))
                document_id = f"{sid}-{index + 1}"
                label = f"synthetic:{sid}/{relative}"
                api.call("PUT", f"/api/workspaces/{wid}/documents/{document_id}", {
                    "title": relative, "text": source.read_text(), "source": label})
                document_map.append({"document_id": document_id, "source": label,
                                     "relative_path": relative, "sha256": digest(source)})
            docs = api.call("GET", f"/api/workspaces/{wid}/documents")
            save(output / sid / "registered-documents.json", docs)
            save(output / sid / "source-fingerprints.json", document_map)
            conversation = api.call("POST", f"/api/workspaces/{wid}/conversations", {
                "title": f"{args.evaluation_label} synthetic {sid}", **scenario["initial_state"]})
            cid = conversation["id"]
            summary["scenarios"].append({"id": sid, "workspace_id": wid,
                                         "conversation_id": cid, "document_count": len(document_map)})
            save(output / sid / "conversation-created.json", conversation)
            states = {}
            for turn in scenario["turns"]:
                tid = turn["id"]
                failed = [dep for dep in turn["depends_on"] if states.get(dep) != "SUCCEEDED"]
                if failed:
                    row = {"scenario": sid, "turn": tid, "status": "BLOCKED",
                           "blocked_by": failed, "task_submitted": False}
                    states[tid] = "BLOCKED"
                    summary["turns"].append(row)
                    save(output / sid / tid / "blocked.json", row)
                    print(f"{tid}: BLOCKED by {','.join(failed)}", flush=True)
                    continue
                task_dir = output / sid / tid
                try:
                    conversation = api.call("GET", f"/api/workspaces/{wid}/conversations/{cid}")
                    docs = api.call("GET", f"/api/workspaces/{wid}/documents")
                    save(task_dir / "conversation-before.json", conversation)
                    request = {"question": turn["question"], "strategy": "bm25", "mode": "agent",
                               "top_k": 5, "approved_revision": docs["revision"],
                               "expected_conversation_revision": conversation["revision"]}
                    if "state_update" in turn:
                        request["state"] = turn["state_update"]
                    save(task_dir / "request.json", request)
                    summary["task_submission_attempts"] += 1
                    if summary["task_submission_attempts"] > 6:
                        raise RuntimeError("Task submission ceiling exceeded")
                    task = api.call("POST", f"/api/workspaces/{wid}/conversations/{cid}/turns",
                                    request, uuid.uuid4().hex)
                    save(task_dir / "submission.json", task)
                    task_id = task["id"]
                    print(f"{tid}: submitted {task_id}", flush=True)
                    deadline = time.monotonic() + args.task_timeout
                    while task["status"] not in TERMINAL:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Polling deadline; provider outcome unknown, no retry")
                        time.sleep(args.poll_seconds)
                        task = api.call("GET", f"/api/workspaces/{wid}/tasks/{task_id}")
                    save(task_dir / "task-final.json", task)
                    save(task_dir / "observed-source-fingerprints.json", observed_fingerprints(task))
                    save(task_dir / "usage.json", {"execution_usage": task.get("execution_usage"),
                                                  "usage_unknown": task.get("usage_unknown"),
                                                  "result_usage": (task.get("result") or {}).get("usage")})
                    conversation = api.call("GET", f"/api/workspaces/{wid}/conversations/{cid}")
                    save(task_dir / "conversation-after.json", conversation)
                    states[tid] = task["status"]
                    row = {"scenario": sid, "turn": tid, "status": task["status"],
                           "task_id": task_id, "task_submitted": True,
                           "error_code": task.get("error_code"), "usage_unknown": task.get("usage_unknown")}
                    summary["turns"].append(row)
                    print(f"{tid}: {task['status']} error={task.get('error_code')}", flush=True)
                except Exception as error:
                    states[tid] = "FAILED"
                    row = {"scenario": sid, "turn": tid, "status": "EVALUATOR_ERROR_OR_UNKNOWN",
                           "error_type": type(error).__name__, "error": str(error),
                           "automatic_retry": False}
                    summary["turns"].append(row)
                    save(task_dir / "error.json", row)
                    print(f"{tid}: {row['status']} {row['error_type']}", flush=True)
                save(output / "run-summary.json", summary)
        save(output / "system-after.json", api.call("GET", "/api/system/status"))
    finally:
        summary["finished_unix"] = time.time()
        save(output / "run-summary.json", summary)
        files = {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
        save(private / "raw-output-manifest.json", {"input_manifest_sha256": expected_manifest,
                                                  "files": files})
        (private / "raw-output-manifest.sha256").write_text(
            digest(private / "raw-output-manifest.json") + "  raw-output-manifest.json\n")
        print(f"output frozen: {digest(private / 'raw-output-manifest.json')}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--evaluation-label", choices=("independent", "development-regression"),
                        default="independent")
    parser.add_argument("--private", type=Path, default=Path(".local/round1-independent"))
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--task-timeout", type=float, default=200)
    run(parser.parse_args())
