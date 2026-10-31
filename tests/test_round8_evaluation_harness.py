"""Offline safety tests for bounded regression collection; no external service or model."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("round8_harness_test", REPO / "scripts/evaluate_conversation_round8.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)
SCORE_SPEC = importlib.util.spec_from_file_location("round8_score_test", REPO / "scripts/score_conversation_round8.py")
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
                     "generation_tokens": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
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
    result = harness.run(SimpleNamespace(approval_file=approval, base_url="http://127.0.0.1:1", run_label="offline-test", go="GO"))
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


def test_root_go_is_required_even_when_runner_is_imported(tmp_path):
    with pytest.raises(RuntimeError, match="Explicit root GO"):
        harness.run(SimpleNamespace(approval_file=tmp_path / "does-not-exist.json"))


def test_changing_run_label_cannot_reuse_once_only_authorization(tmp_path, monkeypatch):
    api = FakeAPI(first="SUCCEEDED")
    result, _ = run_offline(tmp_path, monkeypatch, api)
    assert result == 0 and len(api.submissions) == 10
    with pytest.raises(RuntimeError, match="collection already exists"):
        harness.run(SimpleNamespace(approval_file=tmp_path / "approval.json", base_url="http://127.0.0.1:1",
                                    run_label="another-label", go="GO"))
    assert len(api.submissions) == 10
    assert harness.read(tmp_path / "runs/once-only-claim.json")["run_label"] == "offline-test"


@pytest.mark.parametrize("configuration", [{"decision": 1801, "review": 2600}, {"decision": 1800, "review": 2601},
                                           {"decision": 1800}, {"decision": 1800, "review": 2600, "extra": 1}, None])
def test_boolean_attestation_cannot_hide_changed_output_limits(monkeypatch, configuration):
    approval = harness.read(harness.PREVIOUS / "runs/round7-once/approval.json")
    approval["max_output_configuration"] = configuration
    monkeypatch.setattr(harness, "verify_approved_inputs", lambda *_: {})
    monkeypatch.setattr(harness, "verify_source", lambda *_: {})
    with pytest.raises(RuntimeError, match="1800/2600"):
        harness.verify_approval(approval, approval["endpoint"])


@pytest.mark.parametrize("kind,delivered", [("fact_report", True), ("clarification", True), ("mixed", True), ("no_response", False)])
def test_delivered_clarification_is_explicitly_distinct_from_fact_report(evidence_resolver, kind, delivered):
    record = {"type": kind, "rationale": "Semantic delivery classification", "evidence_pointers": ["raw:task.json#/answer"]}
    assert scoring.validate_delivery(record, delivered, evidence_resolver) == kind


@pytest.mark.parametrize("kind,delivered", [("fact_report", False), ("clarification", False), ("mixed", False), ("no_response", True), ("success", True)])
def test_delivery_annotations_cannot_turn_failed_execution_into_response(evidence_resolver, kind, delivered):
    record = {"type": kind, "rationale": "Semantic delivery classification", "evidence_pointers": ["raw:task.json#/answer"]}
    with pytest.raises(ValueError):
        scoring.validate_delivery(record, delivered, evidence_resolver)


@pytest.mark.parametrize('mutation', ['aggregate_zero', 'aggregate_total', 'request_total'])
def test_known_attempts_do_not_make_inconsistent_token_totals_known(mutation):
    task = harness.read(harness.NEW / 'runs/round5-new-once/kl-t1-final-task.json')
    original = copy.deepcopy(task)
    if mutation == 'aggregate_zero':
        task['execution_usage']['generation_tokens'] = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    elif mutation == 'aggregate_total':
        task['execution_usage']['generation_tokens']['total_tokens'] += 1
    else:
        finished = [e for e in task['result']['agent']['trace'] if e['event'] == 'request_finished']
        finished[0]['usage']['total_tokens'] += 1
    usage, _, known_calls, unknown_tokens = harness.usage_flags(task)
    assert known_calls and unknown_tokens
    assert usage['generation_tokens'] == task['execution_usage']['generation_tokens']
    assert harness.read(harness.NEW / 'runs/round5-new-once/kl-t1-final-task.json') == original


@pytest.fixture
def complete_scoring_fixture(tmp_path, monkeypatch):
    """A fully annotated synthetic ledger tests rejection, not answer quality."""
    repo, run = tmp_path / 'repo', tmp_path / 'run'
    base, old, new = repo / 'eval', repo / 'old', repo / 'new'
    dataset = harness.normalize()
    old_gold = harness.read(harness.OLD / 'gold/rubric.json')
    new_gold = harness.read(harness.NEW / 'frozen/gold.json')
    harness.dump(base / 'frozen/dataset.json', dataset)
    harness.dump(old / 'gold/rubric.json', old_gold)
    harness.dump(new / 'frozen/gold.json', new_gold)
    harness.dump(base / 'input-freeze.json', {'files': {
        str(path.relative_to(repo)): harness.sha(path) for path in
        [base / 'frozen/dataset.json', old / 'gold/rubric.json', new / 'frozen/gold.json']}})
    approved_input = harness.sha(base / 'input-freeze.json')
    source_files = {'fixture.py': 'b' * 64}
    identity = {'match': True, 'candidate_sha': 'a' * 40, 'source_files': source_files}
    harness.dump(run / 'approval.json', {'candidate_sha': 'a' * 40,
        'source_files': source_files, 'input_freeze_sha256': approved_input})
    harness.dump(run / 'source-before.json', identity)
    harness.dump(run / 'source-after.json', identity)
    harness.dump(run / 'inputs-after.json', {'match': True, 'input_freeze_sha256': approved_input})
    harness.dump(run / 'evidence.json', {'answer': 'Synthetic control; not a model answer or quality claim.'})
    outcomes = [{'turn_id': t['id'], 'group': s['group'], 'status': 'SUCCEEDED', 'valid_saved_response': True}
                for s in dataset['scenarios'] for t in s['turns']]
    harness.dump(run / 'run-summary.json', {'candidate_sha': 'a' * 40, 'input_freeze_sha256': approved_input,
        'turns': outcomes, 'known_model_calls': 0, 'unknown_model_call_tasks': [],
        'unknown_token_or_provider_tasks': [], 'fatal': None})
    for field, value in [('REPO', repo), ('BASE', base), ('OLD', old), ('NEW', new)]:
        monkeypatch.setattr(scoring, field, value)
    def seal():
        harness.dump(run / 'raw-seal.json', {'files': {str(p.relative_to(run)): harness.sha(p)
            for p in sorted(run.iterdir()) if p.name != 'raw-seal.json'}})
    seal()
    annotations = tmp_path / 'annotations.json'
    scoring.template(SimpleNamespace(run=run, output=annotations))
    data = harness.read(annotations)
    def prose(record):
        record.update(rationale='Synthetic test annotation, not an assessment of real answers.',
                      evidence_pointers=['raw:evidence.json#/answer'])
    diagnostic_pass = {'core_conclusion': 'correct', 'completeness_gap': 'none', 'erroneous_refusal': 'none',
        'evidence_scope': 'preserved', 'missing_information_handling': 'not_applicable',
        'contradiction_handling': 'not_applicable', 'erroneous_clarification': 'none'}
    for row in data['turns']:
        row['summary'] = 'Synthetic complete-annotation control.'
        row['delivery']['type'] = 'fact_report'
        prose(row['delivery'])
        for key, record in row['diagnostics'].items():
            record['label'] = diagnostic_pass[key]; prose(record)
        for field in ('retrieval_review', 'citation_review'):
            review = {}; prose(review); row[field] = [review]
        if row['group'] == 'cedar-harbor':
            for record in row['axes'].values(): record['status'] = 'pass'; prose(record)
            for record in row['mandatory_checks']: record['status'] = 'pass'; prose(record)
            for record in row['forbidden_checks']: record['present'] = False; prose(record)
        else:
            for record in row['dimensions'].values(): record['score'] = 2; prose(record)
            for record in row['claim_checks']: record['verdict'] = 'satisfied'; prose(record)
    harness.dump(annotations, data)
    def execute():
        data['raw_seal_sha256'] = harness.sha(run / 'raw-seal.json')
        harness.dump(annotations, data)
        output = tmp_path / 'scores.json'
        scoring.score(SimpleNamespace(run=run, annotations=annotations, output=output))
        return harness.read(output)
    return SimpleNamespace(data=data, run=run, seal=seal, execute=execute)


@pytest.mark.parametrize('value', [None, True, '2', -1, 3])
def test_delivered_response_requires_every_numeric_score(complete_scoring_fixture, value):
    fixture = complete_scoring_fixture
    row = next(r for r in fixture.data['turns'] if r['turn_id'] == 'ar-t1')
    row['dimensions']['conclusion_correctness']['score'] = value
    with pytest.raises(ValueError, match='invalid score'):
        fixture.execute()


@pytest.mark.parametrize('dimension,label', [('core_conclusion', 'incorrect'), ('core_conclusion', 'partly_correct'),
    ('completeness_gap', 'explicit_question_omission'), ('erroneous_refusal', 'partial'),
    ('evidence_scope', 'unsupported_inference'), ('missing_information_handling', 'overclaimed'),
    ('contradiction_handling', 'omitted'), ('erroneous_clarification', 'blanket')])
def test_strict_gold_pass_cannot_coexist_with_conflicting_diagnostic(complete_scoring_fixture, dimension, label):
    fixture = complete_scoring_fixture
    row = next(r for r in fixture.data['turns'] if r['turn_id'] == 'ar-t1')
    row['diagnostics'][dimension]['label'] = label
    with pytest.raises(ValueError, match='Strict pass conflicts with diagnostic'):
        fixture.execute()


def test_new_group_pure_clarification_cannot_claim_every_answer_requirement_satisfied(complete_scoring_fixture):
    fixture = complete_scoring_fixture
    next(r for r in fixture.data['turns'] if r['turn_id'] == 'ar-t1')['delivery']['type'] = 'clarification'
    with pytest.raises(ValueError, match='pure clarification'):
        fixture.execute()


def test_old_group_true_ambiguity_may_pass_with_clarification(complete_scoring_fixture):
    fixture = complete_scoring_fixture
    old_gold = scoring.read(scoring.OLD / 'gold/rubric.json')['turns']
    target = next(tid for tid, gold in old_gold.items() if not gold['retrieval_applicable'])
    next(r for r in fixture.data['turns'] if r['turn_id'] == target)['delivery']['type'] = 'clarification'
    scores = fixture.execute()
    assert next(t for t in scores['turns'] if t['turn_id'] == target)['strict_pass'] is True


@pytest.mark.parametrize('error_file', ['source-after-error.json', 'inputs-after-error.json'])
def test_sealed_identity_error_blocks_formal_scoring(complete_scoring_fixture, error_file):
    fixture = complete_scoring_fixture
    harness.dump(fixture.run / error_file, {'type': 'RuntimeError', 'message': 'HEAD changed since candidate approval'})
    fixture.seal()
    with pytest.raises(ValueError, match='identity failed'):
        fixture.execute()


def test_mid_run_identity_failure_cannot_be_hidden_by_restored_final_files(complete_scoring_fixture):
    fixture = complete_scoring_fixture
    summary = harness.read(fixture.run / 'run-summary.json')
    summary['fatal'] = {'type': 'RuntimeError', 'message': 'Manifest file mismatch: lablens/rag.py'}
    harness.dump(fixture.run / 'run-summary.json', summary); fixture.seal()
    with pytest.raises(ValueError, match='identity failed during'):
        fixture.execute()


def test_valid_partial_infrastructure_failure_remains_visible_in_score(complete_scoring_fixture):
    fixture = complete_scoring_fixture
    summary = harness.read(fixture.run / 'run-summary.json')
    summary['fatal'] = {'type': 'ConnectError', 'message': 'Status observation connection failed'}
    harness.dump(fixture.run / 'run-summary.json', summary); fixture.seal()
    scores = fixture.execute()
    assert scores['execution_fatal'] == summary['fatal']
    assert scores['execution_validity'] == {'source_input_identity_valid': True,
        'collection_complete': False, 'status': 'valid_partial_collection'}
    assert [g['planned'] for g in scores['groups'].values()] == [6, 4]


def test_unsealed_execution_ledger_cannot_drive_formal_scoring(complete_scoring_fixture):
    fixture = complete_scoring_fixture
    path = fixture.run / "raw-seal.json"
    seal = harness.read(path)
    del seal["files"]["run-summary.json"]
    harness.dump(path, seal)
    with pytest.raises(ValueError, match="identity evidence is incomplete"):
        fixture.execute()


@pytest.mark.parametrize("existing", ["input-freeze.json", "runs/once-only-claim.json"])
def test_prepare_cannot_replace_frozen_protocol_or_collection(tmp_path, monkeypatch, existing):
    harness.dump(tmp_path / existing, {"preserved": True})
    monkeypatch.setattr(harness, "BASE", tmp_path)
    with pytest.raises(RuntimeError, match="refuse"):
        harness.prepare(SimpleNamespace())
    assert harness.read(tmp_path / existing) == {"preserved": True}


@pytest.fixture
def historical_prepare_fixture(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    base, old, new = repo / "round8", repo / "old", repo / "new"
    earlier, previous = repo / "round6", repo / "round7"
    for root, name in ((old, "frozen-manifest.json"), (new, "freeze-manifest.json")):
        harness.dump(root / "gold.json", {"synthetic_fixture": True})
        harness.dump(root / name, {"files": {"gold.json": harness.sha(root / "gold.json")}})
    for root, label in ((earlier, "round6-once"), (previous, "round7-once")):
        run = root / "runs" / label
        harness.dump(run / "evidence.json", {"synthetic_fixture": True})
        harness.dump(run / "raw-seal.json", {"files": {"evidence.json": harness.sha(run / "evidence.json")}})
        for filename in ("REPORT.md", "execution-notes.md"):
            (root / filename).write_text("Synthetic historical fixture, not a quality claim.\n")
        harness.dump(root / "assessment/scores.json", {"synthetic_fixture": True})
    (previous / "assessment/failure-review.md").write_text("Preserved synthetic failure.\n")
    grade_files = [previous / "assessment" / name for name in ("scores.json", "failure-review.md")]
    harness.dump(previous / "assessment/grading-manifest.json", {
        "raw_seal_sha256": harness.sha(previous / "runs/round7-once/raw-seal.json"),
        "files": [{"path": p.name, "sha256": harness.sha(p)} for p in grade_files]})
    for relative in ("scripts/evaluate_conversation_round8.py", "scripts/score_conversation_round8.py",
                     "tests/test_round8_evaluation_harness.py", "round8/protocol.md"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic prepare fixture.\n")
    for field, value in (("REPO", repo), ("BASE", base), ("OLD", old), ("NEW", new),
                         ("EARLIER", earlier), ("PREVIOUS", previous),
                         ("__file__", str(repo / "scripts/evaluate_conversation_round8.py"))):
        monkeypatch.setattr(harness, field, value)
    monkeypatch.setattr(harness, "normalize", lambda: {"synthetic_fixture": True})
    def no_network(*args, **kwargs):
        raise AssertionError("Preparing input must remain offline")
    monkeypatch.setattr(harness.helpers, "API", no_network)
    return SimpleNamespace(repo=repo, base=base, earlier=earlier, previous=previous)


def test_prepare_binds_prior_scores_failure_review_raw_and_grade_seals(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    harness.prepare(SimpleNamespace())
    manifest = harness.read(fixture.base / "input-freeze.json")
    required = {"round7/assessment/scores.json", "round7/assessment/failure-review.md",
                "round7/assessment/grading-manifest.json", "round7/runs/round7-once/raw-seal.json",
                "round6/assessment/scores.json", "round6/runs/round6-once/raw-seal.json"}
    assert required.issubset(manifest["files"])
    harness.verify_manifest(fixture.repo, manifest)
    assert manifest["planned_turns"] == 10 and manifest["max_model_attempts"] == 70
    assert manifest["original_gold_unchanged"] is True


@pytest.mark.parametrize("target", ["assessment/scores.json", "assessment/failure-review.md",
                                     "runs/round7-once/evidence.json"])
def test_prepare_rejects_mutated_round7_evidence(historical_prepare_fixture, target):
    fixture = historical_prepare_fixture
    with (fixture.previous / target).open("a") as stream:
        stream.write("\n")
    with pytest.raises(RuntimeError, match="Manifest file mismatch"):
        harness.prepare(SimpleNamespace())
    assert not (fixture.base / "input-freeze.json").exists()


def test_prepare_rejects_prior_grading_bound_to_another_raw_seal(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    path = fixture.previous / "assessment/grading-manifest.json"
    value = harness.read(path)
    value["raw_seal_sha256"] = "0" * 64
    harness.dump(path, value)
    with pytest.raises(RuntimeError, match="grading/raw seal identity"):
        harness.prepare(SimpleNamespace())
    assert not (fixture.base / "input-freeze.json").exists()
