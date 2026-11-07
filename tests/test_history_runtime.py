import asyncio
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location("history_runtime", Path(__file__).parents[1] / "scripts/check_history_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def fixture_page(ids):
    return {"view": "summary", "limit": 25, "ordering": "created_at_desc_id_desc", "has_more": True,
            "next_cursor": runtime.cursor_for(ids[-1]), "tasks": [{"id": task, "workspace_id": runtime.fixture.WORKSPACES[0],
                "question": f"Synthetic history question {UUID(task).int - 140000000}", "status": "SUCCEEDED",
                "mode": "extractive", "strategy": "bm25", "snapshot_revision": 1,
                "snapshot_document_count": 1, "usage_unknown": False} for task in ids]}


def stateful_server(monkeypatch, *, reject=None, wrong_reader=False, unknown=False, active_forever=False,
                    wrong_worker=False, missing_metric=False):
    ids = runtime.fixture.hot_ids(100000)
    tasks, calls = {}, []
    workspace = str(UUID(int=999001))
    worker_hash = hashlib.sha256((runtime.ROOT / "scripts/controlled_admission_worker.py").read_bytes()).hexdigest()
    accepted = 0
    original = httpx.AsyncClient

    def handler(request):
        nonlocal accepted
        calls.append((request.method, str(request.url)))
        path = request.url.path
        if path.endswith("/system/status"):
            return httpx.Response(200, json={"database": "available", "workers": {"enabled": True, "concurrency": 2},
                 "task_counts": {"SUCCEEDED": 100000 + (0 if active_forever else accepted), "RUNNING": accepted if active_forever else 0}})
        if "/actuator/metrics/" in path:
            name = path.rsplit("/", 1)[-1]
            kinds = ["COUNT", "TOTAL_TIME", "MAX"] if name.endswith(".acquire") else ["COUNT"] if name.endswith(".timeout") else ["VALUE"]
            return httpx.Response(200, json={"name": name, "measurements": [] if missing_metric else [{"statistic": key, "value": 0} for key in kinds]})
        if path == "/control/status":
            return httpx.Response(200, json={"fixture": "synthetic-bm25-extractive-only", "fixed_delay_ms": 400,
                "started": accepted, "completed": 0 if active_forever else accepted, "active": accepted if active_forever else 0,
                "failed": 0, "provider_calls": 0, "max_active": 2, "worker_sha256": "wrong" if wrong_worker else worker_hash})
        if path == "/api/workspaces" and request.method == "POST":
            return httpx.Response(201, json={"id": workspace})
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "runtime-sentinel", "version": 1})
        if path.endswith("/tasks") and request.method == "POST":
            if reject and len([x for x in calls if x[0] == "POST" and x[1].endswith("/tasks")]) == 1:
                status = 429 if reject in {"queue_full", "rate_limited"} else 503
                return httpx.Response(status, headers={"Retry-After": "1"},
                    json={"error": reject, "retryable": True, "retry_after_seconds": 1})
            accepted += 1
            task = str(UUID(int=800000 + accepted))
            body = json.loads(request.content)
            assert body["mode"] == "extractive" and body["strategy"] == "bm25"
            tasks[task] = {"id": task, "workspace_id": workspace, "status": "RUNNING" if active_forever else "SUCCEEDED",
                "request": body, "execution_usage": runtime.fixture.ZERO, "cache_hit": False, "usage_unknown": unknown,
                "result": {"status": "excerpts"}, "reused": False}
            return httpx.Response(202, json=tasks[task])
        if path.endswith("/tasks"):
            if runtime.fixture.WORKSPACES[1] in path:
                return httpx.Response(400, json={"error": "invalid_request"})
            token = request.url.params.get("cursor")
            start = 0 if not token else next(i + 1 for i, task in enumerate(ids) if runtime.cursor_for(task) == token)
            result = fixture_page(ids[start:start + 25])
            if wrong_reader:
                result["tasks"][0]["result"] = {"private": "should-not-be-listed"}
            return httpx.Response(200, json=result)
        task = path.rsplit("/", 1)[-1]
        if task in tasks:
            return httpx.Response(200, json=tasks[task])
        return httpx.Response(200, json={"id": ids[0], "workspace_id": runtime.fixture.WORKSPACES[0], "cache_hit": False,
            "usage_unknown": False, "execution_usage": runtime.fixture.ZERO,
            "request": {"question": f"Synthetic history question {UUID(ids[0]).int - 140000000}"},
            "result": {"answer": runtime.fixture.RESULT_PADDING}})

    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))
    return calls


def arguments(tmp_path, action):
    jar = tmp_path / "candidate.jar"
    jar.write_bytes(b"offline-test-placeholder-not-an-executed-jar")
    return SimpleNamespace(action=action, endpoint="http://127.0.0.1:8086", worker_endpoint="http://127.0.0.1:8770",
                           output=tmp_path / action, jar=jar, source_commit="a" * 40)


def test_readonly_verifies_real_cursor_contract_and_never_mutates(monkeypatch, tmp_path):
    calls = stateful_server(monkeypatch)
    args = arguments(tmp_path, "read-only")
    result = asyncio.run(runtime.run(args))
    assert result["passed"] is True
    assert all(method == "GET" for method, _ in calls)
    with gzip.open(args.output / "http.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == result["http_observations"] == 17
    assert any(row["label"] == "deep-page" and len(row["body"]["tasks"]) == 25 for row in rows)
    with pytest.raises(FileExistsError):
        asyncio.run(runtime.run(args))


def test_mixed_accounts_for_30_real_attempts_separately_from_history(monkeypatch, tmp_path):
    calls = stateful_server(monkeypatch)
    args = arguments(tmp_path, "mixed")
    result = asyncio.run(runtime.run(args))
    assert result["passed"] is True
    assert result["submissions"] == {"attempted": 30, "accepted": 30, "rejected": 0,
                                    "unconfirmed_or_unexpected": 0, "rejection_codes": {}}
    assert len([row for row in calls if row[0] == "POST" and row[1].endswith("/tasks")]) == 30
    assert len([row for row in calls if "view=summary" in row[1]]) == 160
    tasks = json.loads((args.output / "tasks.json").read_text())
    assert len(tasks["accepted_ids"]) == len(tasks["terminal"]) == 30


@pytest.mark.parametrize("capacity_code", ["queue_full", "global_queue_full"])
def test_mixed_accepts_exact_capacity_backpressure_without_retry(monkeypatch, tmp_path, capacity_code):
    calls = stateful_server(monkeypatch, reject=capacity_code)
    args = arguments(tmp_path, "mixed")
    result = asyncio.run(runtime.run(args))
    assert result["passed"] is True and result["completed"] is True
    assert result["submissions"] == {"attempted": 30, "accepted": 29, "rejected": 1,
        "unconfirmed_or_unexpected": 0, "rejection_codes": {capacity_code: 1}}
    assert len([row for row in calls if row[0] == "POST" and row[1].endswith("/tasks")]) == 30
    assert len(json.loads((args.output / "tasks.json").read_text())["terminal"]) == 29


@pytest.mark.parametrize("fault", ["wrong_reader", "unknown", "missing_metric", "admission_unavailable", "database_unavailable", "rate_limited"])
def test_mixed_preserves_failure_without_retrying_or_discarding_accepted_tasks(monkeypatch, tmp_path, fault):
    rejected = fault in {"admission_unavailable", "database_unavailable", "rate_limited"}
    calls = stateful_server(monkeypatch, **({"reject": fault} if rejected else {fault: True}))
    args = arguments(tmp_path, "mixed")
    result = asyncio.run(runtime.run(args))
    assert result["passed"] is False and result["completed"] is True
    assert len([row for row in calls if row[0] == "POST" and row[1].endswith("/tasks")]) == 30
    terminal = json.loads((args.output / "tasks.json").read_text())["terminal"]
    assert len(terminal) == (29 if rejected else 30)
    assert any(not check["passed"] for check in result["checks"])


def test_wrong_worker_hash_blocks_every_mutation(monkeypatch, tmp_path):
    calls = stateful_server(monkeypatch, wrong_worker=True)
    result = asyncio.run(runtime.run(arguments(tmp_path, "mixed")))
    assert result["passed"] is False and result["completed"] is False
    assert all(method == "GET" for method, _ in calls)


def test_poll_budget_preserves_unfinished_ids_as_failure(monkeypatch, tmp_path):
    stateful_server(monkeypatch, active_forever=True)
    monkeypatch.setattr(runtime, "POLL_SECONDS", .01)
    args = arguments(tmp_path, "mixed")
    result = asyncio.run(runtime.run(args))
    assert result["passed"] is False
    tasks = json.loads((args.output / "tasks.json").read_text())
    assert len(tasks["not_observed_terminal"]) == 30 and tasks["terminal"] == {}


def test_remote_origins_and_unknown_usage_never_pass(tmp_path):
    args = arguments(tmp_path, "mixed")
    args.endpoint = "https://provider.example"
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(runtime.run(args))
    assert not args.output.exists()
    assert runtime.known_zero({"execution_usage": {"generation_tokens": None}}) is False
    assert runtime.valid_page({"view": "summary", "limit": 25, "tasks": [None]}, ["bad"]) is False


@pytest.mark.parametrize("change", [{"http_status": 503}, {"retry_after": None}, {"retryable": False}, {"retry_after_seconds": True}])
def test_malformed_capacity_response_cannot_pass(change):
    row = {"valid_transport": True, "http_status": 429, "retry_after": "1",
           "body": {"error": "queue_full", "retryable": True, "retry_after_seconds": 1}}
    for name, value in change.items():
        (row["body"] if name in row["body"] else row)[name] = value
    assert not runtime.capacity_rejection(row)
