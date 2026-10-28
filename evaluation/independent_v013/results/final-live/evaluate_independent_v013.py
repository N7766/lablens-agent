#!/usr/bin/env python3
"""Capture a frozen twelve-case one-shot run. This does NOT grade semantics.

Gold stays local and is never sent to the application. No automatic retry,
embedding request, environment/key loading, or mutation of the frozen suite.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.request


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def verify_freeze(suite: Path) -> dict:
    manifest = json.loads((suite / "freeze.json").read_text())
    hashes = manifest["files"]
    for name, expected in hashes.items():
        if digest((suite / name).read_bytes()) != expected:
            raise ValueError(f"Frozen input changed: {name}")
    actual = digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())
    if actual != manifest["suite_sha256"]:
        raise ValueError("Invalid frozen root hash")
    return manifest


def verify_runtime_fingerprint(path: Path) -> dict:
    """Hash bytes only; evaluator does not inspect candidate source implementation."""
    fingerprint = json.loads(path.read_text())
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for relative, expected in fingerprint.get("source_sha256", {}).items():
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Runtime fingerprint source escapes repository")
        hashes[relative] = digest(candidate.read_bytes())
        if hashes[relative] != expected:
            raise ValueError(f"Runtime source fingerprint mismatch: {relative}")
    return hashes


def request_payload(case: dict, corpus: dict, manifest: dict, mode: str) -> dict:
    settings = manifest["request_settings"]
    return {
        "question": case["question"],
        "strategy": settings["strategy"],
        "mode": mode,
        "top_k": settings["top_k"],
        "documents": [
            {key: doc[key] for key in ("id", "title", "text", "source")}
            for doc in corpus["documents"]
        ],
        "snapshot_id": "v013-blind-" + manifest["suite_sha256"][:24],
    }


def http_json(url: str, payload: dict | None, timeout: float) -> dict:
    started_at = utc()
    start = time.perf_counter()
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if body is None else "POST",
    )
    status = None
    error = None
    raw = ""
    try:
        # Loopback traffic must not use shell/system HTTP proxies.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=timeout
        ) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
        error = "HTTPError"
    except Exception as exc:
        # Exception type only; avoid accidentally recording sensitive environment data.
        error = type(exc).__name__
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if raw:
            error = error or "InvalidJSON"
    return {
        "started_at_utc": started_at,
        "finished_at_utc": utc(),
        "latency_ms": round((time.perf_counter() - start) * 1000, 3),
        "http_status": status,
        "transport_error": error,
        "response": parsed,
        "unparsed_body": raw if parsed is None else None,
        "retry_count": 0,
    }


def result_body(record: dict) -> dict:
    body = record.get("response")
    if not isinstance(body, dict):
        return {}
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def percentile(values: list[float], quantile: float) -> float | None:
    """Linear interpolation at (n-1)*q; preserve failures in latency distribution."""
    if not values:
        return None
    values = sorted(values)
    i = (len(values) - 1) * quantile
    lower = math.floor(i)
    upper = math.ceil(i)
    return round(values[lower] + (values[upper] - values[lower]) * (i - lower), 3)


def summarize(records: list[dict], expected_count: int) -> dict:
    statuses: dict[str, int] = {}
    totals = {"model_calls": 0, "embedding_requests": 0, "embedding_tokens": 0,
              "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    unknown = 0
    for record in records:
        body = result_body(record)
        status = body.get("status", record.get("transport_error") or "missing_status")
        statuses[status] = statuses.get(status, 0) + 1
        usage = body.get("usage")
        if not isinstance(usage, dict):
            unknown += 1
            continue
        unknown += bool(usage.get("provider_usage_unknown"))
        for key in ("model_calls", "embedding_requests", "embedding_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] += value
        tokens = usage.get("generation_tokens") or {}
        if isinstance(tokens, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(tokens.get(key), (int, float)):
                    totals[key] += tokens[key]
    latencies = [r["latency_ms"] for r in records]
    return {
        "expected_cases": expected_count,
        "captured_cases": len(records),
        "statuses": statuses,
        "successful_http_responses": sum(r.get("http_status") == 200 for r in records),
        "recorded_usage": totals,
        "records_with_missing_or_unknown_usage": unknown,
        "latency_ms_including_failures": {
            "p50": percentile(latencies, .5), "p95": percentile(latencies, .95),
        },
        "semantic_scores": None,
        "semantic_note": "Unscored. A separate AI reviewer must read the frozen rubric and actual evidence.",
    }


def self_test() -> None:
    import tempfile
    checks = 0
    assert percentile([10, 20, 30, 40], .5) == 25
    checks += 1
    assert percentile([10, 20, 30, 40], .95) == 38.5
    checks += 1
    assert percentile([], .5) is None
    checks += 1
    records = [
        {"http_status": 200, "latency_ms": 10, "response": {"status": "answered", "usage": {"model_calls": 2}}},
        {"http_status": 502, "latency_ms": 100, "response": {"detail": {"status": "provider_error", "usage": {"model_calls": 1, "provider_usage_unknown": True}}}},
        {"http_status": None, "latency_ms": 130, "transport_error": "TimeoutError", "response": None},
    ]
    summary = summarize(records, 3)
    assert summary["successful_http_responses"] == 1 and summary["captured_cases"] == 3
    checks += 1
    assert summary["recorded_usage"]["model_calls"] == 3
    checks += 1
    assert summary["records_with_missing_or_unknown_usage"] == 2
    checks += 1
    assert summary["latency_ms_including_failures"]["p50"] == 100
    checks += 1
    assert summary["semantic_scores"] is None
    checks += 1
    manifest = {"request_settings": {"strategy": "bm25", "top_k": 5}, "suite_sha256": "a" * 64}
    payload = request_payload({"question": "Q", "verdict": "gold-secret"},
                              {"documents": [{"id": "a", "title": "T", "text": "X", "source": "S", "gold": "secret"}]}, manifest, "live")
    assert "gold-secret" not in json.dumps(payload) and "secret" not in json.dumps(payload)
    checks += 1
    assert payload["mode"] == "live" and payload["strategy"] == "bm25"
    checks += 1
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)
        (path / "one").write_text("frozen")
        hashes = {"one": digest(b"frozen")}
        write_json(path / "freeze.json", {"files": hashes, "suite_sha256": digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())})
        verify_freeze(path)
        checks += 1
        (path / "one").write_text("changed")
        try:
            verify_freeze(path)
        except ValueError:
            checks += 1
        else:
            raise AssertionError("Changed frozen input was accepted")
    print(json.dumps({"self_test_assertions_passed": checks}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path(".local/independent-v013"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8766")
    parser.add_argument("--mode", choices=["extractive", "live"])
    parser.add_argument("--runtime-fingerprint", type=Path)
    parser.add_argument("--timeout", type=float, default=130)
    parser.add_argument("--concurrency", type=int, choices=[1, 2], default=2)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output or not args.mode or not args.runtime_fingerprint:
        parser.error("--output, --mode and --runtime-fingerprint are required")
    if args.output.exists():
        parser.error("Output already exists; refusing to overwrite or retry a draw")
    manifest = verify_freeze(args.suite)
    runtime_source_before = verify_runtime_fingerprint(args.runtime_fingerprint)
    cases = json.loads((args.suite / "cases.json").read_text())["cases"]
    corpus = json.loads((args.suite / "corpus.json").read_text())
    if len(cases) != 12 or manifest["request_settings"]["strategy"] != "bm25":
        raise ValueError("Unexpected frozen protocol")
    args.output.mkdir(parents=True)
    shutil.copy2(args.runtime_fingerprint, args.output / "runtime-fingerprint.json")
    shutil.copy2(args.suite / "freeze.json", args.output / "suite-freeze.json")
    script_bytes = Path(__file__).read_bytes()
    (args.output / "evaluate_independent_v013.py").write_bytes(script_bytes)
    fingerprint_hash = digest(args.runtime_fingerprint.read_bytes())
    metadata = {
        "started_at_utc": utc(), "mode": args.mode, "endpoint": args.endpoint,
        "http_client_timeout_seconds": args.timeout, "http_concurrency": args.concurrency,
        "retry_count": 0, "suite_sha256": manifest["suite_sha256"],
        "evaluator_script_sha256": digest(script_bytes),
        "runtime_fingerprint_sha256": fingerprint_hash,
        "runtime_source_sha256_before": runtime_source_before,
        "generation_call_reservation": 24 if args.mode == "live" else 0,
        "method": "Capture only; no automated semantic grading or judge model call",
    }
    write_json(args.output / "run.json", metadata)
    status_before = http_json(args.endpoint.rstrip("/") + "/api/rag/status", None, args.timeout)
    write_json(args.output / "status-before.json", status_before)
    expected_status = json.loads(args.runtime_fingerprint.read_text()).get("status", {})
    for key in ("loaded_implementation_sha256", "loaded_assessment_sha256"):
        if expected_status.get(key) and result_body(status_before).get(key) != expected_status[key]:
            raise ValueError(f"Loaded service fingerprint mismatch: {key}")
    records = []

    def call(case: dict) -> dict:
        verify_freeze(args.suite)
        payload = request_payload(case, corpus, manifest, args.mode)
        record = http_json(args.endpoint.rstrip("/") + "/api/rag/answer", payload, args.timeout)
        return {"case_id": case["id"], "request": payload, **record}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        pending = {pool.submit(call, case): case["id"] for case in cases}
        for future in as_completed(pending):
            record = future.result()
            records.append(record)
            write_json(args.output / (record["case_id"] + ".json"), record)
            body = result_body(record)
            print(json.dumps({"completed": len(records), "total": 12,
                              "case_id": record["case_id"], "http_status": record["http_status"],
                              "status": body.get("status"), "latency_ms": record["latency_ms"]}), flush=True)
    records.sort(key=lambda row: row["case_id"])
    write_json(args.output / "responses.json", records)
    summary = summarize(records, len(cases))
    summary["extractive_schema_preflight_pass"] = (
        args.mode == "extractive" and all(
            r["http_status"] == 200
            and isinstance(result_body(r).get("retrieval", {}).get("hits"), list)
            and result_body(r).get("usage", {}).get("model_calls") == 0
            for r in records
        )
    ) if args.mode == "extractive" else None
    write_json(args.output / "capture-summary.json", summary)
    status_after = http_json(args.endpoint.rstrip("/") + "/api/rag/status", None, args.timeout)
    write_json(args.output / "status-after.json", status_after)
    verify_freeze(args.suite)
    metadata.update({
        "finished_at_utc": utc(), "frozen_inputs_unchanged": True,
        "evaluator_source_unchanged": digest(Path(__file__).read_bytes()) == metadata["evaluator_script_sha256"],
        "runtime_fingerprint_file_unchanged": digest(args.runtime_fingerprint.read_bytes()) == fingerprint_hash,
        "runtime_source_sha256_after": verify_runtime_fingerprint(args.runtime_fingerprint),
        "loaded_service_fingerprints_unchanged": all(
            result_body(status_before).get(key) == result_body(status_after).get(key)
            for key in ("loaded_implementation_sha256", "loaded_assessment_sha256")
        ),
    })
    write_json(args.output / "run.json", metadata)
    print(json.dumps({"complete": True, **summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
