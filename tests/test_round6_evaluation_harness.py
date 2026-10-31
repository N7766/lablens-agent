"""Offline safety tests for bounded regression collection; no external service or model."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("round6_harness_test", REPO / "scripts/evaluate_conversation_round6.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)
SCORE_SPEC = importlib.util.spec_from_file_location("round6_score_test", REPO / "scripts/score_conversation_round6.py")
scoring = importlib.util.module_from_spec(SCORE_SPEC)
SCORE_SPEC.loader.exec_module(scoring)


def test_normalization_preserves_source_text_questions_dependencies_and_state():
    normalized = harness.normalize()
    old = harness.read(harness.OLD / "inputs/scenarios.json")["scenarios"]
    new = harness.read(harness.NEW / "frozen/dataset.json")["scenarios"]
    assert len(normalized["scenarios"]) == 4
    assert sum(len(s["documents"]) for s in normalized["scenarios"]) == 16
    assert sum(len(s["turns"]) for s in normalized["scenarios"]) == 10
    for original, actual in zip(old, normalized["scenarios"][:2]):
        assert actual["turns"] == original["turns"]
        assert {k: actual["conversation"][k] for k in original["initial_state"]} == original["initial_state"]
        for document, path in zip(actual["documents"], sorted((harness.OLD / original["workspace_relative"]).rglob("*.md"))):
            assert document["text"] == path.read_text()
    for original, actual in zip(new, normalized["scenarios"][2:]):
        assert actual["documents"] == original["documents"]
        assert actual["conversation"] == original["conversation"]
        for old_turn, new_turn in zip(original["turns"], actual["turns"]):
            assert new_turn["question"] == old_turn["question"]
            assert new_turn["depends_on"] == ([old_turn["depends_on"]] if old_turn["depends_on"] else [])


@pytest.mark.parametrize("turn,expected_unknown", [("kl-t1", False), ("ar-t1", True)])
def test_known_attempts_and_unknown_token_outcome_are_separate(turn, expected_unknown):
    task = harness.read(harness.NEW / f"runs/round5-new-once/{turn}-final-task.json")
    usage, origin, known, unknown_tokens = harness.usage_flags(task)
    assert origin == "execution_usage" and known is True
    assert unknown_tokens is expected_unknown
    assert harness.audit_trace_attempts(task, usage["model_calls"]) == {"decision": 2, "review": 1}


def test_zero_usage_is_distinct_from_missing_usage():
    zero = {"execution_usage": {"model_calls": 0, "provider_usage_unknown": False,
            "generation_tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
            "result": {"agent": {"trace": []}}}
    assert harness.usage_flags(zero)[2:] == (True, False)
    assert harness.usage_flags({})[2:] == (False, True)


def test_trace_phase_guard_rejects_two_reviews_within_total_ceiling():
    task = {"result": {"agent": {"trace": [{"event": "request_started", "phase": "review"}] * 2}}}
    with pytest.raises(RuntimeError, match="Per-phase"):
        harness.audit_trace_attempts(task, 2)


def test_trace_and_execution_attempts_must_agree():
    task = {"result": {"agent": {"trace": [{"event": "request_started", "phase": "decision"}]}}}
    with pytest.raises(RuntimeError, match="disagree"):
        harness.audit_trace_attempts(task, 2)


class FakeAPI:
    """Records calls and returns synthetic completed tasks without network access."""

    def __init__(self, first="FAILED", unknown_calls=False, unknown_tokens=False, after_submission=None):
        self.first, self.unknown_calls, self.unknown_tokens = first, unknown_calls, unknown_tokens
        self.after_submission = after_submission
        self.submissions = []
        self.conversations = {}
        self.counter = 0

    def request(self, method, path, payload=None, key=None, timeout=20):
        if path == "/api/system/status":
            return {"test_fixture": True}
        if path == "/api/workspaces":
            self.counter += 1
            return {"id": "workspace-" + str(self.counter)}
        if "/documents" in path:
            return {"revision": 4}
        if path.endswith("/conversations") and method == "POST":
            cid = "conversation-" + str(self.counter)
            self.conversations[path + "/" + cid] = {"id": cid, "revision": 0, "turns": []}
            return copy.deepcopy(self.conversations[path + "/" + cid])
        if path.endswith("/turns"):
            self.submissions.append((path, payload, key))
            status = self.first if len(self.submissions) == 1 else "SUCCEEDED"
            tid = "task-" + str(len(self.submissions))
            conversation = self.conversations[path[:-6]]
            conversation["revision"] += 1
            conversation["turns"].append({"task_id": tid, "status": status, "answer": "fixture answer" if status == "SUCCEEDED" else ""})
            unknown = self.unknown_tokens and len(self.submissions) == 1
            usage = {"model_calls": None if self.unknown_calls else 3, "provider_usage_unknown": unknown,
                     "generation_tokens": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                     "embedding_requests": 0, "embedded_inputs": 0, "cache_reused": False}
            trace = []
            for index, phase in enumerate(("decision", "decision", "review")):
                trace.extend([{"event": "request_started", "phase": phase, "request_id": str(index)},
                              {"event": "request_finished", "phase": phase, "request_id": str(index),
                               "usage_known": True, "response_received": True, "provider_acceptance": "response_received",
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}])
            result = {"agent": {"trace": trace}}
            if self.after_submission:
                self.after_submission(len(self.submissions))
            return {"id": tid, "status": status, "execution_usage": usage, "usage_unknown": unknown,
                    "result": result if status == "SUCCEEDED" else None, "error_result": result if status != "SUCCEEDED" else None}
        if path in self.conversations:
            return copy.deepcopy(self.conversations[path])
        raise AssertionError((method, path))


def run_offline(tmp_path, monkeypatch, api):
    harness.dump(tmp_path / "frozen/dataset.json", harness.normalize())
    harness.dump(tmp_path / "input-freeze.json", {"offline_test_fixture": True,
                 "files": {"frozen/dataset.json": harness.sha(tmp_path / "frozen/dataset.json")}})
    approval = tmp_path / "approval.json"
    harness.dump(approval, {"candidate_sha": "a" * 40, "input_freeze_sha256": harness.sha(tmp_path / "input-freeze.json")})
    monkeypatch.setattr(harness, "BASE", tmp_path)
    monkeypatch.setattr(harness, "REPO", tmp_path)
    monkeypatch.setattr(harness, "verify_approval", lambda *_: {"test_fixture": True})
    monkeypatch.setattr(harness, "verify_source", lambda *_: {"test_fixture": True})
    monkeypatch.setattr(harness.helpers, "API", lambda *_: api)
    result = harness.run(SimpleNamespace(approval_file=approval, base_url="http://127.0.0.1:1", run_label="offline-test"))
    run = tmp_path / "runs/offline-test"
    summary = harness.read(run / "run-summary.json")
    harness.verify_manifest(run, harness.read(run / "raw-seal.json"))
    return result, summary


def test_failed_predecessor_blocks_dependents_but_preserves_all_ten_denominator(tmp_path, monkeypatch):
    api = FakeAPI(first="FAILED")
    result, summary = run_offline(tmp_path, monkeypatch, api)
    assert result == 0
    assert len(summary["turns"]) == 10 and len(api.submissions) == 8
    by_id = {t["turn_id"]: t for t in summary["turns"]}
    assert by_id["cedar-t2"]["status"] == by_id["cedar-t3"]["status"] == "blocked_no_valid_prior_response"
    assert by_id["harbor-t1"]["status"] == "SUCCEEDED"
    assert summary["known_model_calls"] == 24


def test_unknown_attempt_count_stops_all_further_submissions_without_retry(tmp_path, monkeypatch):
    api = FakeAPI(first="SUCCEEDED", unknown_calls=True)
    result, summary = run_offline(tmp_path, monkeypatch, api)
    assert result == 1 and len(api.submissions) == 1
    assert summary["unknown_model_call_tasks"] == ["cedar-t1"]
    assert sum(t["status"].startswith("blocked") for t in summary["turns"]) == 9


def test_unknown_tokens_with_known_attempts_remain_unknown_and_do_not_force_retry(tmp_path, monkeypatch):
    api = FakeAPI(first="SUCCEEDED", unknown_tokens=True)
    result, summary = run_offline(tmp_path, monkeypatch, api)
    assert result == 0 and len(api.submissions) == 10
    assert summary["known_model_calls"] == 30 and summary["unknown_model_call_tasks"] == []
    assert summary["unknown_token_or_provider_tasks"] == ["cedar-t1"]
    assert len({key for _, _, key in api.submissions}) == 10


@pytest.mark.parametrize("mutation", ["negative_aggregate", "negative_request", "unclosed", "usage_unknown", "acceptance_unknown"])
def test_aggregate_known_usage_cannot_hide_request_uncertainty(mutation):
    task = harness.read(harness.NEW / "runs/round5-new-once/kl-t1-final-task.json")
    trace = task["result"]["agent"]["trace"]
    last = [e for e in trace if e.get("event") == "request_finished"][-1]
    if mutation == "negative_aggregate":
        task["execution_usage"]["generation_tokens"]["total_tokens"] = -1
    elif mutation == "negative_request":
        last["usage"]["completion_tokens"] = -1
    elif mutation == "unclosed":
        trace.remove(last)
    elif mutation == "usage_unknown":
        last["usage_known"] = False
    else:
        last["provider_acceptance"] = "unknown"
    assert harness.usage_flags(task)[2:] == (True, True)


@pytest.mark.parametrize("target", ["manifest", "file"])
@pytest.mark.parametrize("after_count", [1, 10])
def test_input_mutation_stops_submissions_and_preserves_approved_identity(tmp_path, monkeypatch, target, after_count):
    def mutate(count):
        if count == after_count:
            if target == "manifest":
                path = tmp_path / "input-freeze.json"
                value = harness.read(path)
                value["tampered"] = True
                harness.dump(path, value)
            else:
                with (tmp_path / "frozen/dataset.json").open("a") as stream:
                    stream.write("\n")
    api = FakeAPI(first="SUCCEEDED", after_submission=mutate)
    result, summary = run_offline(tmp_path, monkeypatch, api)
    approval = harness.read(tmp_path / "approval.json")
    assert result == 1 and len(api.submissions) == after_count
    assert summary["input_freeze_sha256"] == approval["input_freeze_sha256"]
    assert (tmp_path / "runs/offline-test/inputs-after-error.json").is_file()


@pytest.fixture
def evidence_resolver(tmp_path, monkeypatch):
    repo, run = tmp_path / "repo", tmp_path / "run"
    base = repo / "eval"
    harness.dump(run / "task.json", {"answer": "supported", "claims": [{"value": 2}], "unknown": None})
    harness.dump(repo / "gold.json", {"required": ["answer"]})
    (run / "extra.txt").write_text("exists but not sealed\n")
    (repo / "notes.md").write_text("line one\nline two\n")
    harness.dump(run / "raw-seal.json", {"files": {"task.json": harness.sha(run / "task.json")}})
    harness.dump(base / "input-freeze.json", {"files": {"gold.json": harness.sha(repo / "gold.json"),
                                                        "notes.md": harness.sha(repo / "notes.md")}})
    monkeypatch.setattr(scoring, "REPO", repo)
    monkeypatch.setattr(scoring, "BASE", base)
    return scoring.EvidenceResolver(run)


@pytest.mark.parametrize("pointer", [None, "", " ", "raw:missing.json", "raw:extra.txt", "raw:../task.json",
                                      "raw:/task.json", "raw:task.json#/missing", "raw:task.json#/unknown",
                                      "raw:task.json#/claims/1", "raw:task.json#/claims/-1",
                                      "input:notes.md#L0", "input:notes.md#L1-L3", "input:notes.md#L2-L1"])
def test_scoring_rejects_missing_null_unsealed_or_out_of_range_pointer(evidence_resolver, pointer):
    with pytest.raises(ValueError):
        scoring.prose({"rationale": "human semantic review", "evidence_pointers": [pointer]}, "test", evidence_resolver)


@pytest.mark.parametrize("pointer", ["raw:task.json", "raw:task.json#/answer", "raw:task.json#/claims/0/value",
                                      "input:gold.json#/required/0", "input:notes.md#L1-L2"])
def test_scoring_accepts_locatable_sealed_raw_and_input_references(evidence_resolver, pointer):
    scoring.prose({"rationale": "human semantic review", "evidence_pointers": [pointer]}, "test", evidence_resolver)
