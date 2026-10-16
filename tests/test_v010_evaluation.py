from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from lablens.datasets import DatasetBundle
from lablens.guided_planning import ProposalV2, compile_proposal, draft_guided
from lablens.plan_checks import clauses
from lablens.planning import AnalysisPlan
from lablens.task_checks import check_tasks

spec = importlib.util.spec_from_file_location('evaluate_tasks', Path(__file__).resolve().parents[1] / 'scripts/evaluate_tasks.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
CASES = evaluation.load_cases()
CONTROLS = evaluation.load_controls()
BY_ID = {case['id']: case for case in CASES}


def compile_message(case, message):
    return compile_proposal(case['question'], ProposalV2.model_validate_json(message['tool_calls'][0]['function']['arguments']))


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_literal_oracle_and_positive_proposal_preserve_all_three_tasks(case, monkeypatch):
    def forbidden():
        pytest.fail('Offline evaluation must not create a provider client')
    monkeypatch.setattr(evaluation, 'HTTPModel', forbidden)
    trace = evaluation.execute_case(case)
    assert trace['contract']['contract_pass'], trace['contract']['failures']
    assert trace['receipt_check']['verified']
    assert trace['model_calls'] == trace['live_provider_calls'] == 0
    assert trace['first_attempt_contract']['contract_pass'] is None
    bundle = evaluation.load_bundle(case)
    assert [step['request_excerpt'] for step in case['expected_steps']] == [c['text'] for c in clauses(case['question'])]
    plan = compile_message(case, evaluation.proposal_message(case))
    checked = check_tasks(case['question'], plan, run_ids=[r['id'] for r in bundle['runs']])
    assert len(checked['units']) == 2 and all(u['state'] == 'matched' for u in checked['units'])
    drafted = draft_guided(evaluation.FirstReply(evaluation.proposal_message(case)), case['question'], max_attempts=1,
                           check_request_rules=True, check_task_rules=True, dataset=DatasetBundle.model_validate(bundle),
                           proposal_version='v2')
    evaluation.execute_prepared(case, bundle, drafted)
    assert evaluation.grade_contract(case, bundle, drafted)['contract_pass']


def test_snapshots_are_existing_unchanged_data_and_comparison_math_is_independent():
    root = evaluation.ROOT
    dpo = DatasetBundle(runs=json.loads((root/'lablens/data/runs.json').read_text()),
                        notes=json.loads((root/'lablens/data/notes.json').read_text())).normalized()
    assert evaluation.load_bundle(CASES[0]) == dpo
    assert all(not row['synthetic'] for row in dpo['runs'])
    assert (root/'evaluation/v0.10/fixtures/snapshot-atlas.json').read_bytes() == (root/'evaluation/v0.9/fixtures/snapshot-atlas.json').read_bytes()
    for case in CASES:
        bundle = evaluation.load_bundle(case)
        assert evaluation.snapshot_digest(bundle) == case['expected_snapshot_id']
        pair = case['expected_steps'][1]
        rows = {r['id']: r for r in bundle['runs']}
        a, b = rows[pair['baseline_id']], rows[pair['candidate_id']]
        assert pair['additional_correct'] == b['correct'] - a['correct']
        assert pair['accuracy_delta_percentage_points'] == pytest.approx(100*(b['correct']-a['correct'])/a['num_samples'])
        assert pair['length_reduction_percent'] == pytest.approx(100*(a['mean_length']-b['mean_length'])/a['mean_length'])
        if rows[pair['baseline_id']]['synthetic']:
            assert all(r['synthetic'] for r in bundle['runs'])
            for row in rows.values():
                assert hashlib.sha256((root/row['source_path']).read_bytes()).hexdigest() == row['source_sha256']
    prior = {c['question'] for c in json.loads((root/'evaluation/v0.9/cases.json').read_text())}
    assert not prior.intersection(c['question'] for c in CASES)


@pytest.mark.parametrize('control', CONTROLS, ids=lambda c:c['id'])
def test_injected_negative_and_positive_controls_are_not_natural_draws(control, monkeypatch):
    monkeypatch.setattr(evaluation, 'HTTPModel', lambda: pytest.fail('Offline controls must not call provider'))
    case = BY_ID[control['case_id']]
    off = evaluation.execute_control(case, control, arm='task_rules_off')
    assert off['status'] == 'completed'
    assert not off['contract']['contract_pass']
    assert off['receipt_check']['verified']  # Recomputable wrong task remains wrong.
    assert off['planner_completion_calls'] == 1 and off['live_provider_calls'] == 0
    on = evaluation.execute_control(case, control)
    assert on['contract']['contract_pass'], on['contract']['failures']
    assert not on['first_attempt_contract']['contract_pass']
    assert on['planner_completion_calls'] == 2 and on['live_provider_calls'] == 0
    assert on['injected_first_proposal'] and not on['natural_model_draw']
    assert on['review_mode'] == 'authored_positive_control'
    reports = [e['report'] for e in on['events'] if e['type'] == 'task_check']
    assert reports[0]['has_conflicts'] and not reports[-1]['has_conflicts']
    row = evaluation.summarize(case, on, 1)
    assert 'model_calls' not in row and 'repair_calls' not in row
    aggregate = evaluation.aggregate([row], suite='controls')
    assert aggregate['natural_model_draws'] == 0 and aggregate['live_provider_calls'] == 0


def test_live_injected_repair_is_one_billed_request_and_retains_authored_seed(monkeypatch):
    control = CONTROLS[0]
    case = BY_ID[control['case_id']]
    instances = []
    class Repair:
        def __init__(self):
            self.calls = 0
            instances.append(self)
        def complete(self, messages, tools):
            self.calls += 1
            assert messages[-1]['role'] == 'tool'
            assert 'task-rule conflict' in messages[-1]['content']
            return evaluation.proposal_message(case), dict(prompt_tokens=20, completion_tokens=10, estimated_cost=0.005)
    monkeypatch.setattr(evaluation, 'HTTPModel', Repair)
    trace = evaluation.execute_control(case, control, live=True)
    assert len(instances) == 1 and instances[0].calls == 1
    assert trace['planner_completion_calls'] == 2 and trace['live_provider_calls'] == 1
    assert trace['contract']['contract_pass'] and not trace['first_attempt_contract']['contract_pass']
    replies = [e for e in trace['events'] if e['type'] == 'model']
    assert replies[0]['message'] == evaluation.injected_message(case, control)
    assert replies[0]['usage']['source'] == 'authored_fault_not_billed'
    row = evaluation.summarize(case, trace, 1)
    assert row['reported_estimated_cost_usd'] == pytest.approx(0.005)


def test_natural_first_score_replay_is_zero_extra_provider_requests(monkeypatch):
    case = CASES[0]
    instances = []
    class TwoReplies:
        mode = 'fake_live'
        def __init__(self):
            self.calls = 0
            instances.append(self)
        def complete(self, messages, tools):
            self.calls += 1
            message = evaluation.injected_message(case, CONTROLS[0]) if self.calls == 1 else evaluation.proposal_message(case)
            return message, dict(prompt_tokens=10, completion_tokens=5, estimated_cost=0.01)
    monkeypatch.setattr(evaluation, 'HTTPModel', TwoReplies)
    trace = evaluation.execute_case(case, live=True)
    assert len(instances) == 1 and instances[0].calls == 2
    assert trace['model_calls'] == trace['live_provider_calls'] == 2
    assert trace['contract']['contract_pass'] and not trace['first_attempt_contract']['contract_pass']
    assert trace['first_attempt_contract']['additional_provider_requests'] == 0
    row = evaluation.summarize(case, trace, 1)
    result = evaluation.aggregate([row])
    assert result['first_contract_pass'] == 0 and result['contract_pass'] == 1
    assert result['repair_calls'] == result['repaired_contract_success'] == 1
    assert result['known_reported_estimated_cost_usd'] == pytest.approx(0.02)


@pytest.mark.parametrize('question', ['“以run:sft为基线比较run:dpo-best的准确率差”',
                                     '以run:unknown为基线比较run:dpo-best的准确率差',
                                     '解释验证集会如何影响可信度',
                                     '不要以run:sft为基线比较run:dpo-best的准确率差'])
def test_unknown_quoted_or_negated_tasks_are_abstentions_not_success(question):
    plan = AnalysisPlan(steps=[dict(id='description', kind='describe', request_excerpt=question)], unhandled=[])
    report = check_tasks(question, plan, run_ids=[r['id'] for r in evaluation.load_bundle(CASES[0])['runs']])
    assert report['units'] == [] and report['status'] == 'not_assessed'
    assert report['unassessed'] and report['semantic_validation'] == 'not_performed'


def test_freeze_records_identical_protocol_two_arms_and_all_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'HTTPModel', lambda: pytest.fail('Freeze must precede provider client'))
    manifest = evaluation.freeze_sources(tmp_path, live=True, repeats=2, workers=2)
    assert manifest['arms'] == list(evaluation.ARMS)
    assert manifest['max_provider_requests_per_draw'] == 2
    for file in ('scripts/evaluate_tasks.py', 'scripts/evaluate_snapshots.py', 'lablens/task_checks.py',
                 'evaluation/v0.10/cases.json', 'evaluation/v0.10/controls.json',
                 'evaluation/v0.10/rubric.md', 'evaluation/v0.9/fixtures/source-atlas.json'):
        assert file in manifest['source_sha256']
    for file, sha in manifest['source_sha256'].items():
        raw = (tmp_path/'frozen-source'/file).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha
        assert raw == (evaluation.ROOT/file).read_bytes()
    assert (tmp_path/manifest['protocol']['prompt_file']).read_text() == evaluation.GUIDED_SYSTEM_V2
    for kind in ('prompt','schema'):
        assert hashlib.sha256((tmp_path/manifest['protocol'][kind+'_file']).read_bytes()).hexdigest() == manifest['protocol'][kind+'_sha256']
    for control in CONTROLS:
        declared = manifest['control_proposals'][control['id']]
        case = BY_ID[control['case_id']]
        for kind, message in [('first', evaluation.injected_message(case, control)),
                              ('positive', evaluation.proposal_message(case))]:
            raw = (tmp_path/declared[kind+'_file']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == declared[kind+'_sha256']
            assert json.loads(raw) == message


def test_configuration_failure_is_retained_and_unknown_billing_not_zero(monkeypatch):
    def fail():
        raise RuntimeError('Test provider unavailable')
    monkeypatch.setattr(evaluation, 'HTTPModel', fail)
    trace = evaluation.execute_case(CASES[0], live=True)
    assert trace['status'] == 'harness_error' and not trace['contract']['contract_pass']
    assert trace['first_attempt_contract']['status'] == 'no_model_reply'
    trace.update(model_calls=1,live_provider_calls=1,events=[dict(type='model',usage={})])
    row = evaluation.summarize(CASES[0], trace,1)
    assert row['usage_incomplete'] and row['reported_estimated_cost_usd'] is None


def test_declared_frozen_bundle_used_instead_of_mutable_loader(monkeypatch):
    case = CASES[0]
    bundle = evaluation.load_bundle(case)
    monkeypatch.setattr(evaluation, 'load_bundle', lambda _: pytest.fail('Must use supplied frozen snapshot'))
    assert evaluation.execute_case(case,bundle=deepcopy(bundle))['contract']['contract_pass']
