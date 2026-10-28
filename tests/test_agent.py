import json
from lablens.policy import BOUNDARY
import pytest
from fastapi.testclient import TestClient
from lablens.agent import Agent, DEMO_QUESTION
from lablens.api import app
from lablens.model import ReplayModel, HTTPModel, ModelError
from lablens.tools import ToolBox


def call(name="list_runs", args=None, call_id="c1"):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


def answer(sources=None, status="answered"):
    return {"role": "assistant", "content": json.dumps({"status": status, "claims": [{"text": "Evidence claim", "source_ids": sources or ["run:sft"]}], "limitations": [BOUNDARY]})}


class SequenceModel:
    mode = "test_double"

    def __init__(self, sequence):
        self.sequence = iter(sequence)

    def complete(self, messages, tools):
        return next(self.sequence), {}


def test_replay_is_explicit_and_has_evidence():
    result = Agent(ReplayModel()).run(DEMO_QUESTION)
    assert result["status"] == "completed"
    assert result["mode"] == "scripted_replay"
    assert result["tool_calls"] == 3
    assert result["source_validation"] == "passed"
    assert result["factuality_review"] == "not_performed"
    assert "0.649" in result["answer"]["claims"][0]["text"]


def test_comparison_uses_percentage_points_and_words():
    out = ToolBox().execute("compare_runs", {"baseline_id": "run:sft", "candidate_id": "run:dpo-best"})
    assert out["additional_correct"] == 3
    assert out["accuracy_delta_percentage_points"] == pytest.approx(3 / 462 * 100)
    assert out["length_reduction_percent"] == pytest.approx(2.7571934, abs=1e-6)
    assert out["baseline"]["length_unit"] == "whitespace_words"
    assert "note:validation" in out["source_ids"]


def test_unknown_id():
    with pytest.raises(ValueError, match="Unknown run"):
        ToolBox().execute("compare_runs", {"baseline_id": "../secrets", "candidate_id": "run:sft"})


def test_incompatible_splits():
    tools = ToolBox()
    tools.index["run:sft"]["split_id"] = "other"
    with pytest.raises(ValueError, match="Incompatible"):
        tools.execute("compare_runs", {"baseline_id": "run:sft", "candidate_id": "run:dpo-best"})


@pytest.mark.parametrize("name,args", [("shell", {}), ("list_runs", {"min_accuracy": 1.1}), ("list_runs", {"path": "/etc/passwd"}), ("search_notes", {"query": "", "limit": 3}), ("search_notes", {"query": "token", "limit": 100}), ("compare_runs", {"baseline_id": "run:sft"})])
def test_rejects_invalid_tool_arguments(name, args):
    with pytest.raises(ValueError):
        ToolBox().execute(name, args)


@pytest.mark.parametrize("query,expected", [("词数", "note:units"), ("验证集", "note:validation"), ("latency", "note:latency"), ("significance", "note:significance")])
def test_retrieval_chinese_and_english(query, expected):
    out = ToolBox().execute("search_notes", {"query": query, "limit": 1})
    assert out["source_ids"] == [expected]


def test_retrieval_no_match_has_no_fabricated_sources():
    assert ToolBox().execute("search_notes", {"query": "zzzzzzzz"})["source_ids"] == []


def test_unread_source_rejected_then_repaired():
    model = SequenceModel([answer(), call(), answer()])
    result = Agent(model).run("compare")
    assert result["status"] == "completed"
    assert any(e["type"] == "validation_error" for e in result["events"])


def test_fake_source_never_passes():
    result = Agent(SequenceModel([call(), answer(["fake"])]), max_steps=2).run("q")
    assert result["status"] == "budget_exceeded"
    assert result["answer"] is None


def test_tool_error_recovery():
    result = Agent(SequenceModel([call("shell"), call(call_id="c2"), answer()])).run("q")
    assert result["status"] == "completed"
    assert "error" in result["events"][1]["output"]


def test_batch_cannot_exceed_call_budget():
    batch = call()
    batch["tool_calls"].extend(call(call_id="c2")["tool_calls"])
    result = Agent(SequenceModel([batch]), max_calls=1).run("q")
    assert result["status"] == "budget_exceeded"
    assert result["tool_calls"] == 0


def test_repeated_call_ids_are_protocol_error():
    result = Agent(SequenceModel([call(), call()])).run("q")
    assert result["status"] == "model_error"


def test_invalid_json_answer_consumes_budget():
    result = Agent(SequenceModel([{"content": "not json"}] * 2), max_steps=2).run("q")
    assert result["status"] == "validation_stalled"
    assert result["model_calls"] == 2


@pytest.mark.parametrize("question", ["", "  ", "x" * 2001])
def test_invalid_question(question):
    with pytest.raises(ValueError):
        Agent(ReplayModel()).run(question)


def test_api_demo_and_input_validation():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/api/demo").json()["mode"] == "scripted_replay"
    assert client.post("/api/run", json={"question": "   "}).status_code == 422
    assert client.post("/api/run", json={"question": "x", "key": "bad"}).status_code == 422


def test_missing_key_is_not_silently_replay(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    client = TestClient(app)
    response = client.post("/api/run", json={"question": "compare"})
    assert response.status_code == 503
    assert "LLM_API_KEY" in response.json()["detail"]


def test_remote_http_rejected(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.com")
    with pytest.raises(ModelError, match="HTTPS"):
        HTTPModel()


def test_provider_error_does_not_expose_body_or_key(monkeypatch):
    import httpx
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-secret")
    def failure(*args, **kwargs):
        return httpx.Response(401, text="sensitive provider details", request=httpx.Request("POST", args[0]))
    monkeypatch.setattr(httpx, "post", failure)
    result = Agent(HTTPModel()).run("q")
    assert result["status"] == "model_error"
    assert "401" in result["error"]
    assert "sensitive" not in json.dumps(result)
    assert "not-a-real-secret" not in json.dumps(result)


def test_transport_payload_and_usage(monkeypatch):
    import httpx
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    def response(url, **kwargs):
        assert url == "https://example.com/v1/chat/completions"
        assert kwargs["json"]["tools"][0]["type"] == "function"
        assert "tool_choice" not in kwargs["json"]  # Legacy callers retain provider default behavior.
        return httpx.Response(200, json={"choices": [{"message": answer()}], "usage": {"total_tokens": 42}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", response)
    message, usage = HTTPModel().complete([], ToolBox().schemas())
    assert usage["total_tokens"] == 42
    assert message["role"] == "assistant"


def insufficient():
    return {"role": "assistant", "content": json.dumps({"status": "insufficient_evidence", "claims": [], "limitations": [BOUNDARY]})}


def test_unsearched_abstention_is_rejected():
    result = Agent(SequenceModel([insufficient()]), max_steps=1).run("What is the length unit?")
    assert result["status"] == "budget_exceeded"
    assert result["answer"] is None
    assert "No evidence lookup" in result["events"][-1]["error"]


def test_abstention_after_empty_search_is_allowed():
    result = Agent(SequenceModel([call("search_notes", {"query": "zzzzzzzz"}), insufficient()])).run("Missing evidence")
    assert result["status"] == "completed"
    assert result["answer"]["status"] == "insufficient_evidence"


def test_timeout_is_counted_without_automatic_retry(monkeypatch):
    import httpx
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "90")
    requests = []
    def timeout(*args, **kwargs):
        requests.append(kwargs["timeout"])
        raise httpx.ReadTimeout("sensitive transport message")
    monkeypatch.setattr(httpx, "post", timeout)
    result = Agent(HTTPModel()).run("q")
    assert requests == [90]
    assert result["model_calls"] == 1
    assert result["usage_incomplete"] is True
    assert "timed out" in result["error"]
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("timeout", ["0", "invalid"])
def test_invalid_timeout_rejected(monkeypatch, timeout):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ModelError, match="LLM_TIMEOUT_SECONDS"):
        HTTPModel()
