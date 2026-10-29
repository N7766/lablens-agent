#!/usr/bin/env python3
"""Single-shot, loopback JVM conversation holdout runner; no direct provider access."""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
TASK_LIMIT = 6
CALL_LIMIT = 42
CALLS_PER_TASK = 7
AGENT_SECONDS = 75
OBSERVATION_SECONDS = 200


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def check_authoring():
    manifest = ROOT / "manifest.sha256"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, filename = line.split("  ", 1)
        target = ROOT / filename
        if target.parent != ROOT or digest(target.read_bytes()) != expected:
            raise SystemExit("Frozen authoring manifest mismatch: " + filename)
    fixture = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))
    assert len(fixture["scenarios"]) == 2
    assert sum(len(s["documents"]) for s in fixture["scenarios"]) == 7
    assert sum(len(s["turns"]) for s in fixture["scenarios"]) == 6
    for scenario in fixture["scenarios"]:
        assert len(scenario["turns"]) == 3
        previous = None
        for turn in scenario["turns"]:
            assert turn["depends_on"] == previous
            previous = turn["id"]
    return digest(manifest.read_bytes()), fixture


class HTTPFailure(RuntimeError):
    pass


class Client:
    def __init__(self, base, raw):
        self.base = base
        self.raw = raw
        self.counter = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, label, body=None, key=None):
        self.counter += 1
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(self.base + path, data=payload, headers=headers, method=method)
        record = {"at_utc": now(), "method": method, "path": path, "request_body": body,
                  "idempotency_key": key, "label": label}
        started = time.monotonic()
        response_data = None
        error = None
        try:
            with self.opener.open(request, timeout=15) as response:
                record["http_status"] = response.status
                record["response_headers"] = dict(response.headers.items())
                record["response_body_text"] = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            record["http_status"] = exc.code
            record["response_body_text"] = exc.read().decode("utf-8", errors="replace")
            error = "HTTP " + str(exc.code)
        except Exception as exc:
            record["transport_error"] = type(exc).__name__ + ": " + str(exc)
            error = record["transport_error"]
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        if "response_body_text" in record:
            try:
                response_data = json.loads(record["response_body_text"])
                record["response_json"] = response_data
            except json.JSONDecodeError:
                if record.get("http_status") != 204:
                    error = error or "Non-JSON response"
        filename = f"http-{self.counter:03d}-{label}.json"
        write_json(self.raw / filename, record)
        if error:
            raise HTTPFailure(filename + ": " + error)
        return response_data, filename


def clarification_text(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"clarification_question", "clarification_prompt"} and isinstance(item, str) and item.strip():
                return item
            found = clarification_text(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = clarification_text(item)
            if found:
                return found
    return ""


def model_usage(task):
    usage = task.get("execution_usage")
    return usage if isinstance(usage, dict) else None


def freeze_raw(raw):
    paths = sorted(p for p in raw.iterdir() if p.is_file() and p.name != "manifest.sha256")
    data = "".join(f"{digest(p.read_bytes())}  {p.name}\n" for p in paths)
    (raw / "manifest.sha256").write_text(data, encoding="utf-8")
    return digest(data.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--go")
    parser.add_argument("--qa-url")
    parser.add_argument("--source-freeze")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--service-max-model-calls", type=int)
    parser.add_argument("--service-agent-seconds", type=int)
    parser.add_argument("--service-provider-retries", type=int)
    args = parser.parse_args()
    author_hash, fixture = check_authoring()
    if args.validate_only:
        print(json.dumps({"validated": True, "manifest_sha256": author_hash,
                          "tasks": 6, "documents": 7, "network_calls": 0}))
        return
    if args.go != "GO" or not args.qa_url or not args.source_freeze:
        raise SystemExit("Explicit GO, QA URL, and source-freeze identifier are required")
    if args.expected_manifest_sha256 != author_hash:
        raise SystemExit("Expected authoring freeze SHA-256 does not match")
    if (args.service_max_model_calls, args.service_agent_seconds, args.service_provider_retries) != (7, 75, 0):
        raise SystemExit("Required frozen service contract is 7 calls, 75 seconds, 0 provider retries")
    parsed = urllib.parse.urlsplit(args.qa_url)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("QA URL must be plain loopback HTTP without credentials/query/fragment")
    try:
        assert ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, AssertionError):
        raise SystemExit("QA URL requires an explicit loopback IP")
    if parsed.path.rstrip("/") not in {"", "/api"}:
        raise SystemExit("QA URL path must be empty or /api")
    base = args.qa_url.rstrip("/")
    if not base.endswith("/api"):
        base += "/api"
    raw = ROOT / "raw"
    raw.mkdir(exist_ok=False)  # A run directory is single-use; no selective reruns.
    run_id = "r4-" + uuid.uuid4().hex[:12]
    metadata = {"evaluation_id": fixture["evaluation_id"], "run_id": run_id,
                "started_utc": now(), "qa_url": base, "source_freeze": args.source_freeze,
                "authoring_manifest_sha256": author_hash, "explicit_go": True,
                "service_contract": {"max_model_calls_per_task": 7, "agent_seconds": 75, "provider_retries": 0},
                "limits": {"tasks": 6, "model_calls": 42, "client_observation_seconds": 200},
                "provenance": fixture["provenance"]}
    write_json(raw / "run-metadata.json", metadata)
    client = Client(base, raw)
    records = []
    submitted = 0
    global_stop = None
    try:
        client.request("GET", "/system/status", "system-before")
        for scenario in fixture["scenarios"]:
            setup = {"scenario_id": scenario["id"], "workspace_id": None, "conversation_id": None}
            try:
                workspace, _ = client.request("POST", "/workspaces", scenario["id"] + "-create-workspace",
                                               {"name": run_id + "-" + scenario["id"]})
                wsid = workspace["id"]
                setup["workspace_id"] = wsid
                ws = "/workspaces/" + urllib.parse.quote(str(wsid), safe="")
                for document in scenario["documents"]:
                    client.request("PUT", ws + "/documents/" + document["id"], document["id"] + "-put",
                                   {k: document[k] for k in ("title", "text", "source")})
                documents, _ = client.request("GET", ws + "/documents", scenario["id"] + "-snapshot")
                approved_revision = documents["revision"]
                conversation, _ = client.request("POST", ws + "/conversations", scenario["id"] + "-create-conversation",
                                                 {"title": scenario["title"]})
                cid = conversation["id"]
                setup["conversation_id"] = cid
                cp = ws + "/conversations/" + urllib.parse.quote(str(cid), safe="")
                revision = conversation["revision"]
                predecessor_ok = True
                blocked_reason = None
            except Exception as exc:
                predecessor_ok = False
                blocked_reason = "setup_failure: " + type(exc).__name__ + ": " + str(exc)
            write_json(raw / (scenario["id"] + "-setup.json"), setup)
            for turn in scenario["turns"]:
                record = {"round_id": turn["id"], "scenario_id": scenario["id"],
                          "question": turn["question"], "depends_on": turn["depends_on"],
                          "submitted": False, "delivery": "blocked"}
                if not predecessor_ok or global_stop:
                    record["blocked_reason"] = global_stop or blocked_reason
                    records.append(record)
                    print(json.dumps({"round": turn["id"], "delivery": "blocked"}), flush=True)
                    continue
                task = None
                task_path = None
                body = {"question": turn["question"], "strategy": "bm25", "mode": "agent", "top_k": 5,
                        "approved_revision": approved_revision, "expected_conversation_revision": revision}
                record["request"] = body
                start = time.monotonic()
                try:
                    assert submitted < TASK_LIMIT
                    key = run_id + "-" + turn["id"]
                    # Count uncertain submissions conservatively; never retry them.
                    submitted += 1
                    record["submitted"] = True
                    record["idempotency_key"] = key
                    task, artifact = client.request("POST", cp + "/turns", turn["id"] + "-submit", body, key)
                    record["submission_artifact"] = artifact
                    record["task_id"] = task["id"]
                    task_path = ws + "/tasks/" + urllib.parse.quote(str(task["id"]), safe="")
                    while task.get("status") not in TERMINAL and time.monotonic() - start < OBSERVATION_SECONDS:
                        time.sleep(min(2, max(0, OBSERVATION_SECONDS - (time.monotonic() - start))))
                        if time.monotonic() - start >= OBSERVATION_SECONDS:
                            break
                        task, artifact = client.request("GET", task_path, turn["id"] + "-poll")
                        record["last_task_artifact"] = artifact
                    record["status"] = task.get("status")
                    record["execution_usage"] = model_usage(task)
                    write_json(raw / (turn["id"] + "-final-observed-task.json"), task)
                    if task.get("status") not in TERMINAL:
                        raise RuntimeError("observation_timeout_after_200_seconds")
                    detail, artifact = client.request("GET", cp, turn["id"] + "-persisted-conversation")
                    record["conversation_artifact"] = artifact
                    revision = detail["revision"]
                    current = next((t for t in detail.get("turns", []) if str(t.get("task_id")) == str(task["id"])), {})
                    answer = current.get("answer")
                    if not isinstance(answer, str):
                        answer = ""
                    clarifying = clarification_text(task.get("result"))
                    record["persisted_answer"] = answer
                    record["persisted_clarification"] = clarifying
                    record["answer_truncated"] = current.get("answer_truncated")
                    predecessor_ok = task.get("status") == "SUCCEEDED" and bool(answer.strip() or clarifying.strip())
                    record["delivery"] = "pass" if predecessor_ok else "delivery_failure"
                    if not predecessor_ok:
                        blocked_reason = turn["id"] + " has no usable terminal response"
                    usage = record["execution_usage"]
                    known_calls = usage.get("model_calls") if usage else None
                    if isinstance(known_calls, int) and known_calls > CALLS_PER_TASK:
                        global_stop = "Observed model_calls exceeds frozen seven-call per-task contract"
                except Exception as exc:
                    predecessor_ok = False
                    record["delivery"] = "delivery_failure"
                    record["failure"] = type(exc).__name__ + ": " + str(exc)
                    blocked_reason = turn["id"] + ": " + record["failure"]
                    if task_path and task and task.get("status") not in TERMINAL:
                        try:
                            cleanup, artifact = client.request("POST", task_path + "/cancel", turn["id"] + "-cleanup-cancel")
                            record["cleanup"] = {"artifact": artifact, "status": cleanup.get("status"),
                                                 "separate_from_result": True}
                        except Exception as cleanup_exc:
                            record["cleanup_error"] = str(cleanup_exc)
                record["observation_elapsed_seconds"] = round(time.monotonic() - start, 6)
                records.append(record)
                write_json(raw / "outcomes-in-progress.json", records)
                print(json.dumps({"round": turn["id"], "delivery": record["delivery"],
                                  "status": record.get("status"), "elapsed_seconds": record["observation_elapsed_seconds"],
                                  "execution_usage": record.get("execution_usage")}, ensure_ascii=False), flush=True)
        client.request("GET", "/system/status", "system-after")
    except Exception as exc:
        metadata["run_error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        present = {r["round_id"] for r in records}
        for scenario in fixture["scenarios"]:
            for turn in scenario["turns"]:
                if turn["id"] not in present:
                    records.append({"round_id": turn["id"], "scenario_id": scenario["id"],
                                    "question": turn["question"], "depends_on": turn["depends_on"],
                                    "submitted": False, "delivery": "blocked",
                                    "blocked_reason": metadata.get("run_error", "run_aborted")})
        metadata["ended_utc"] = now()
        metadata["submitted_tasks"] = submitted
        metadata["maximum_possible_model_calls_under_frozen_contract"] = submitted * CALLS_PER_TASK
        metadata["headline_denominator"] = 6
        write_json(raw / "run-metadata.json", metadata)
        write_json(raw / "outcomes.json", records)
        progress = raw / "outcomes-in-progress.json"
        if progress.exists():
            progress.unlink()
        raw_hash = freeze_raw(raw)
        print(json.dumps({"raw_frozen": True, "raw_manifest_sha256": raw_hash,
                          "submitted_tasks": submitted, "rounds_in_denominator": 6}), flush=True)


if __name__ == "__main__":
    main()
