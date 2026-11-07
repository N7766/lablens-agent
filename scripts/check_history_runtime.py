#!/usr/bin/env python3
"""Post-benchmark HTTP contracts and bounded mixed work on synthetic Round14 history."""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("history_fixture", ROOT / "scripts/evaluate_history_scale.py")
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
SEED_SIZE, PAGE_SIZE, SUBMISSIONS, SUBMITTERS, READERS, READS = 100000, 25, 30, 4, 8, 20
POLL_SECONDS = 30
METRICS = ("hikaricp.connections.active", "hikaricp.connections.pending", "hikaricp.connections.max",
           "hikaricp.connections.acquire", "hikaricp.connections.timeout")
EXCLUDED = {"request", "snapshot_json", "result", "result_json", "error_result", "error_result_json",
            "execution_usage", "execution_usage_json"}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def origin(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost") or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("A loopback HTTP origin is required")
    return value.rstrip("/")


def cursor_for(task_id):
    number = UUID(task_id).int - 140000000
    stamp = fixture.timestamp(number).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = f"{fixture.WORKSPACES[0]}|{stamp}|{task_id}".encode("ascii")
    return "v1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def summary_path(workspace=None, cursor=None):
    query = {"view": "summary", "limit": str(PAGE_SIZE)}
    if cursor is not None:
        query["cursor"] = cursor
    return f"/api/workspaces/{workspace or fixture.WORKSPACES[0]}/tasks?" + urlencode(query)


def valid_page(body, expected):
    if not isinstance(body, dict) or body.get("view") != "summary" or body.get("limit") != PAGE_SIZE:
        return False
    rows = body.get("tasks")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows) or [row.get("id") for row in rows] != expected:
        return False
    if body.get("ordering") != "created_at_desc_id_desc" or body.get("has_more") is not True:
        return False
    if body.get("next_cursor") != cursor_for(expected[-1]):
        return False
    for row in rows:
        number = UUID(row["id"]).int - 140000000
        if (EXCLUDED.intersection(row) or row.get("workspace_id") != fixture.WORKSPACES[0]
                or row.get("question") != f"Synthetic history question {number}"
                or row.get("status") != "SUCCEEDED" or row.get("mode") != "extractive"
                or row.get("strategy") != "bm25" or row.get("snapshot_revision") != 1
                or row.get("snapshot_document_count") != 1 or row.get("usage_unknown") is not False):
            return False
    return True


def known_zero(task):
    usage = task.get("execution_usage")
    tokens = usage.get("generation_tokens") if isinstance(usage, dict) else None
    return (isinstance(usage, dict) and task.get("cache_hit") is False and task.get("usage_unknown") is False
            and usage.get("provider_usage_unknown") is False and usage.get("cache_reused") is False
            and all(type(usage.get(key)) is int and usage[key] == 0 for key in
                    ("model_calls", "embedding_requests", "embedded_inputs", "embedding_cache_hits", "embedding_tokens"))
            and isinstance(tokens, dict) and all(type(tokens.get(key)) is int and tokens[key] == 0
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")))


def valid_metric(name, row):
    if not ok(row) or not isinstance(row.get("body"), dict) or row["body"].get("name") != name:
        return False
    measurements = row["body"].get("measurements")
    if not isinstance(measurements, list):
        return False
    values = {item.get("statistic"): item.get("value") for item in measurements if isinstance(item, dict)}
    required = {"COUNT", "TOTAL_TIME", "MAX"} if name.endswith(".acquire") else {"COUNT"} if name.endswith(".timeout") else {"VALUE"}
    return required <= values.keys() and all(type(values[key]) in (int, float) and math.isfinite(values[key]) and values[key] >= 0 for key in required)


class Ledger:
    def __init__(self, output):
        self.raw = (output / "http.jsonl.gz").open("xb")
        self.log = gzip.GzipFile(filename="", fileobj=self.raw, mode="wb", mtime=0)
        self.started = time.monotonic()
        self.count = 0
        self.records = []
        self.inflight = self.peak = 0

    async def request(self, client, method, url, *, label, body=None, key=None):
        self.count += 1
        row = {"sequence": self.count, "label": label, "method": method, "url": url,
               "offset_s": time.monotonic() - self.started, "valid_transport": False}
        if key is not None:
            row["idempotency_key"] = key
        if body is not None:
            row["request_body"] = body
        started = time.monotonic()
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            response = await client.request(method, url, json=body, headers={"Idempotency-Key": key} if key else {})
            row.update(http_status=response.status_code, body_bytes=len(response.content),
                       body_sha256=digest(response.content), retry_after=response.headers.get("Retry-After"))
            if len(response.content) > 128 * 1024:
                row["error_type"] = "ResponseBudgetExceeded"
                row["body_omitted"] = True
            else:
                row["body"] = response.json()
                row["valid_transport"] = True
        except asyncio.CancelledError:
            row["error_type"] = "PollingBudgetCancelled"
            raise
        except Exception as error:
            row["error_type"] = type(error).__name__
        finally:
            row["elapsed_ms"] = (time.monotonic() - started) * 1000
            self.inflight -= 1
            self.records.append(row)
            self.log.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            self.log.flush()
        return row

    def close(self):
        self.log.close()
        self.raw.close()


def ok(row, status=200):
    return row.get("valid_transport") is True and row.get("http_status") == status


def capacity_rejection(row):
    body = row.get("body")
    return (row.get("valid_transport") is True and isinstance(body, dict)
            and (row.get("http_status"), body.get("error")) in {(429, "queue_full"), (503, "global_queue_full")}
            and body.get("retryable") is True and type(body.get("retry_after_seconds")) is int
            and body["retry_after_seconds"] == 1 and row.get("retry_after") == "1")


async def run(args):
    endpoint, worker = origin(args.endpoint), origin(args.worker_endpoint)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), ROOT / "scripts/evaluate_history_scale.py",
               ROOT / "scripts/controlled_admission_worker.py", ROOT / "lablens/rag.py",
               ROOT / "docs/evaluations/conversation-round14/mixed-protocol.md"]
    hashes = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sources}
    run_id = str(uuid4())
    result = {"run_id": run_id, "action": args.action, "passed": False, "completed": False, "checks": [],
              "scope": "100000 synthetic terminal history fixtures plus, for mixed only, 30 real offline task attempts; not production or LLM capacity"}
    dump(args.output / "manifest.json", {"run_id": run_id, "action": args.action, "endpoint": endpoint,
         "worker_endpoint": worker if args.action == "mixed" else None, "seed_rows": SEED_SIZE,
         "hot_workspace": fixture.WORKSPACES[0], "source_commit": args.source_commit,
         "jar_sha256": digest(args.jar.read_bytes()), "source_hashes": hashes,
         "created_at_utc": datetime.now(timezone.utc).isoformat(), "automatic_mutation_retries": 0,
         "configuration": {"submissions": SUBMISSIONS, "submitters": SUBMITTERS, "readers": READERS,
             "reads_each": READS, "page_size": PAGE_SIZE, "poll_budget_seconds": POLL_SECONDS, "http_timeout_seconds": 5}})
    dump(args.output / "summary.json", result)
    ledger = Ledger(args.output)

    def check(name, passed, evidence=None):
        result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})

    async with httpx.AsyncClient(trust_env=False, timeout=5, limits=httpx.Limits(max_connections=16)) as client:
        async def request(method, path, label, body=None, key=None, worker_call=False):
            return await ledger.request(client, method, (worker if worker_call else endpoint) + path,
                                        label=label, body=body, key=key)

        async def metrics(side):
            readings = {}
            for name in METRICS:
                row = await request("GET", "/actuator/metrics/" + name, side + ":" + name)
                readings[name] = row
            dump(args.output / ("resources-" + side + ".json"), readings)
            check(side + " connection metrics available", all(valid_metric(name, row) for name, row in readings.items()))

        try:
            before = await request("GET", "/api/system/status", "system-before")
            before_body = before.get("body", {})
            check("Exactly the 100000 terminal synthetic fixtures precede this action",
                  ok(before) and before_body.get("database") == "available"
                  and sum(before_body.get("task_counts", {}).values()) == SEED_SIZE
                  and before_body.get("task_counts", {}).get("SUCCEEDED") == SEED_SIZE)
            if not result["checks"][-1]["passed"]:
                raise RuntimeError("Isolated fixture precondition failed; no mutations sent")
            await metrics("before")
            expected = fixture.hot_ids(SEED_SIZE)
            if args.action == "read-only":
                first = await request("GET", summary_path(), "first-page")
                check("First summary page matches exact seed values and excludes large JSON", ok(first) and valid_page(first.get("body"), expected[:PAGE_SIZE]))
                token = first.get("body", {}).get("next_cursor")
                if not isinstance(token, str):
                    raise RuntimeError("Missing first-page continuation; no replacement cursor invented")
                second = await request("GET", summary_path(cursor=token), "second-page")
                check("Returned continuation gives the exact second page", ok(second) and valid_page(second.get("body"), expected[PAGE_SIZE:PAGE_SIZE * 2]))
                offset = int(len(expected) * .8)
                deep = await request("GET", summary_path(cursor=cursor_for(expected[offset - 1])), "deep-page")
                check("Known 80-percent boundary gives the exact deep page", ok(deep) and valid_page(deep.get("body"), expected[offset:offset + PAGE_SIZE]))
                foreign = await request("GET", summary_path(fixture.WORKSPACES[1], token), "cross-workspace-cursor")
                check("Cross-workspace cursor returns 400", ok(foreign, 400) and foreign["body"].get("error") == "invalid_request")
                detail = await request("GET", f"/api/workspaces/{fixture.WORKSPACES[0]}/tasks/{expected[0]}", "full-detail")
                detail_body = detail.get("body", {})
                check("Full detail retains the exact saved synthetic report and usage", ok(detail)
                      and detail_body.get("id") == expected[0] and detail_body.get("workspace_id") == fixture.WORKSPACES[0]
                      and detail_body.get("result", {}).get("answer") == fixture.RESULT_PADDING
                      and detail_body.get("request", {}).get("question") == f"Synthetic history question {UUID(expected[0]).int - 140000000}"
                      and known_zero(detail_body))
            else:
                worker_before = await request("GET", "/control/status", "worker-before", worker_call=True)
                controlled = worker_before.get("body", {})
                check("Fresh fixed-delay provider-forbidden worker and enabled two-slot dispatcher",
                      ok(worker_before) and controlled.get("fixture") == "synthetic-bm25-extractive-only"
                      and controlled.get("fixed_delay_ms") == 400 and controlled.get("started") == 0
                      and controlled.get("provider_calls") == 0 and controlled.get("active") == 0
                      and controlled.get("worker_sha256") == hashes["scripts/controlled_admission_worker.py"]
                      and before_body.get("workers", {}).get("enabled") is True
                      and before_body.get("workers", {}).get("concurrency") == 2)
                if not result["checks"][-1]["passed"]:
                    raise RuntimeError("Controlled-worker precondition failed; no mutations sent")
                created = await request("POST", "/api/workspaces", "create-mixed-workspace", {"name": "Synthetic mixed history " + run_id})
                if not ok(created, 201) or not isinstance(created.get("body", {}).get("id"), str):
                    raise RuntimeError("Workspace creation not confirmed; never retried")
                workspace = created["body"]["id"]
                result["mixed_workspace"] = workspace
                document = await request("PUT", f"/api/workspaces/{workspace}/documents/runtime-sentinel", "create-mixed-document",
                    {"title": "Synthetic runtime sentinel", "text": "runtime_sentinel is OFFLINE_ONLY. Synthetic fixture, not user evidence.", "source": "synthetic:round14-runtime"})
                if not ok(document):
                    raise RuntimeError("Document creation not confirmed; never retried")
                submissions, reads = [], []

                async def submitter(number):
                    for index in range(number, SUBMISSIONS, SUBMITTERS):
                        body = {"question": f"Find runtime_sentinel for {run_id} request {index}", "mode": "extractive", "strategy": "bm25", "top_k": 1}
                        row = await request("POST", f"/api/workspaces/{workspace}/tasks", f"submit-{index}", body, f"runtime-{run_id}-{index}")
                        submissions.append(row)

                async def reader(number):
                    for index in range(READS):
                        row = await request("GET", summary_path(), f"reader-{number}-{index}")
                        reads.append(row)

                began = time.monotonic()
                await asyncio.gather(*(submitter(i) for i in range(SUBMITTERS)), *(reader(i) for i in range(READERS)))
                result["mixed_submission_and_read_seconds"] = time.monotonic() - began
                accepted = [row for row in submissions if ok(row, 202) and isinstance(row.get("body", {}).get("id"), str)
                            and row["body"].get("workspace_id") == workspace and row["body"].get("reused") is False]
                rejected = [row for row in submissions if capacity_rejection(row)]
                ids = [row["body"]["id"] for row in accepted]
                result["submissions"] = {"attempted": len(submissions), "accepted": len(accepted), "rejected": len(rejected),
                    "unconfirmed_or_unexpected": len(submissions) - len(accepted) - len(rejected),
                    "rejection_codes": dict(Counter(row.get("body", {}).get("error", "missing") for row in rejected))}
                check("All 30 attempts have distinct accepted IDs or exact capacity rejection contracts",
                      len(submissions) == SUBMISSIONS and bool(ids) and len(ids) == len(set(ids))
                      and len(accepted) + len(rejected) == SUBMISSIONS, result["submissions"])
                check("All 160 concurrent-reader responses preserve summary content", len(reads) == READERS * READS and all(ok(row) and valid_page(row.get("body"), expected[:PAGE_SIZE]) for row in reads))
                pending = set(ids)
                terminal = {}
                deadline = time.monotonic() + POLL_SECONDS
                while pending and time.monotonic() < deadline:
                    async def observe(task_id):
                        row = await request("GET", f"/api/workspaces/{workspace}/tasks/{task_id}", "poll-" + task_id)
                        body = row.get("body", {})
                        if ok(row) and body.get("id") == task_id and body.get("workspace_id") == workspace and body.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                            terminal[task_id] = body
                            pending.discard(task_id)
                    try:
                        await asyncio.wait_for(asyncio.gather(*(observe(task_id) for task_id in list(pending))), max(.001, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if pending:
                        await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
                dump(args.output / "tasks.json", {"accepted_ids": ids, "terminal": terminal, "not_observed_terminal": sorted(pending)})
                check("Every accepted task reached SUCCEEDED with known zero usage and no cache", bool(ids) and not pending and len(terminal) == len(ids)
                      and all(task.get("status") == "SUCCEEDED" and known_zero(task) for task in terminal.values()))
                worker_after = await request("GET", "/control/status", "worker-after", worker_call=True)
                state = worker_after.get("body", {})
                check("Controlled-worker completions reconcile with accepted IDs and remain provider-free", ok(worker_after)
                      and state.get("started") == state.get("completed") == len(ids) and state.get("failed") == 0
                      and state.get("active") == 0 and state.get("max_active", 999) <= 2 and state.get("provider_calls") == 0)
            after = await request("GET", "/api/system/status", "system-after")
            counts = after.get("body", {}).get("task_counts", {})
            expected_total = SEED_SIZE + (result.get("submissions", {}).get("accepted", 0) if args.action == "mixed" else 0)
            check("Final persisted count separates unchanged history from accepted new tasks", ok(after)
                  and sum(counts.values()) == expected_total and counts.get("SUCCEEDED") == expected_total, counts)
            await metrics("after")
            result["completed"] = True
        except BaseException as error:
            result["failure_type"] = type(error).__name__
            result["failure_scope"] = "Partial HTTP ledger retained; uncertain mutations must not be retried automatically"
            check("Action completed without an unhandled setup or observation failure", False)
        finally:
            ledger.close()
            result["http_observations"] = len(ledger.records)
            result["max_client_in_flight"] = ledger.peak
            result["elapsed_seconds"] = time.monotonic() - ledger.started
            result["http_log_compressed_bytes"] = (args.output / "http.jsonl.gz").stat().st_size
            check("Frozen sources unchanged", hashes == {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sources})
            result["passed"] = result["completed"] and all(row["passed"] for row in result["checks"])
            dump(args.output / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("read-only", "mixed"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:8086")
    parser.add_argument("--worker-endpoint", default="http://127.0.0.1:8770")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if not args.jar.is_file() or not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("An actual jar and exact source commit are required")
    result = asyncio.run(run(args))
    print(json.dumps({"action": args.action, "passed": result["passed"], "http_observations": result["http_observations"]}))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
