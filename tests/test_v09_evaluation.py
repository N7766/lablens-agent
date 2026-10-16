from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from lablens.datasets import DatasetBundle
from lablens.plan_checks import clauses
from lablens.planning import PlanRequest, execute_plan, preview_plan

spec = importlib.util.spec_from_file_location('evaluate_selection', Path(__file__).resolve().parents[1] / 'scripts/evaluate_selection.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
CASES = evaluation.load_cases()


def reexecute(case, trace):
    request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=evaluation.load_bundle(case))
    trace['preview'] = preview_plan(request)
    request.preview_id = trace['preview']['preview_id']
    trace['execution'] = execute_plan(request)
    return trace


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_new_literal_oracles_execute_without_api(case, monkeypatch):
    def forbidden():
        pytest.fail('Offline oracle must not construct HTTPModel')
    monkeypatch.setattr(evaluation, 'HTTPModel', forbidden)
    trace = evaluation.execute_case(case)
    assert trace['contract']['contract_pass'], trace['contract']['failures']
    assert trace['receipt_check']['verified']
    assert trace['model_calls'] == 0
    assert trace['first_attempt_contract']['contract_pass'] is None
    assert len(trace['execution']['results']) == 3
    assert [s['request_excerpt'] for s in case['expected_steps']] == [c['text'] for c in clauses(case['question'])]


def test_new_fixtures_are_distinct_synthetic_sources_and_literal_deltas_are_correct():
    old_ids = {r['id'] for case in evaluation.load_cases('dev') for r in evaluation.load_bundle(case)['runs']}
    for case in CASES:
        bundle = evaluation.load_bundle(case)
        DatasetBundle.model_validate(bundle).normalized()
        assert evaluation.snapshot_digest(bundle) == case['expected_snapshot_id']
        assert not old_ids.intersection(r['id'] for r in bundle['runs'])
        for row in bundle['runs']:
            assert row['synthetic'] is True
            raw = (evaluation.ROOT / row['source_path']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
        pair = case['expected_steps'][1]
        rows = {r['id']: r for r in bundle['runs']}
        a, b = rows[pair['baseline_id']], rows[pair['candidate_id']]
        assert pair['additional_correct'] == b['correct'] - a['correct']
        assert pair['accuracy_delta_percentage_points'] == pytest.approx(100 * (b['correct'] - a['correct']) / a['num_samples'])
        assert pair['length_reduction_percent'] == pytest.approx(100 * (a['mean_length'] - b['mean_length']) / a['mean_length'])
    dev = evaluation.load_cases('dev')
    assert {c['id'] for c in dev} == {'a-pareto', 'b-pareto'}
    assert all('Known v0.8' in c['development_origin'] for c in dev)


def test_full_frontier_has_multiple_points_and_exact_coordinate_ties():
    expected = CASES[0]['expected_steps'][0]['ordered_run_ids']
    assert len(expected) == 6
    rows = {r['id']: r for r in evaluation.load_bundle(CASES[0])['runs']}
    assert rows['run:atlas-02']['accuracy'] == rows['run:atlas-03']['accuracy']
    assert rows['run:atlas-02']['mean_length'] == rows['run:atlas-03']['mean_length']
    assert 'run:atlas-02' in expected and 'run:atlas-03' in expected


@pytest.mark.parametrize('parameter,wrong', [('top_k', 1), ('min_correct', 191), ('pareto_only', False)])
def test_empty_result_does_not_hide_wrong_scope_or_filters(parameter, wrong):
    case = CASES[4]
    trace = evaluation.execute_case(case)
    trace['plan']['steps'][0]['query'][parameter] = wrong
    reexecute(case, trace)
    assert trace['execution']['results'][0]['result']['ordered_run_ids'] == []
    assert evaluation.receipt_check(trace)['verified']
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('plan parameter ' + parameter in failure for failure in grade['failures'])


def test_pareto_top_two_scope_cannot_pass_just_because_ordinary_top_two_matches():
    case = CASES[2]
    trace = evaluation.execute_case(case)
    ids = trace['execution']['results'][0]['result']['ordered_run_ids']
    trace['plan']['steps'][0]['query']['pareto_only'] = False
    reexecute(case, trace)
    assert trace['execution']['results'][0]['result']['ordered_run_ids'] == ids
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('parameter pareto_only' in failure for failure in grade['failures'])


def test_all_is_unbounded_even_when_large_top_k_returns_same_members():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    ids = trace['execution']['results'][0]['result']['ordered_run_ids']
    trace['plan']['steps'][0]['query']['top_k'] = 100
    reexecute(case, trace)
    assert trace['execution']['results'][0]['result']['ordered_run_ids'] == ids
    assert not evaluation.grade_contract(case, evaluation.load_bundle(case), trace)['contract_pass']


def test_full_set_does_not_invent_order_requirements():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    trace['plan']['steps'][0]['query'].update(order_by='mean_length', descending=False, include_ties=False)
    reexecute(case, trace)
    assert evaluation.grade_contract(case, evaluation.load_bundle(case), trace)['contract_pass']


def test_top_k_and_extreme_control_require_requested_cutoff_and_ties():
    top, minimum = CASES[6], CASES[7]
    assert top['expected_steps'][0]['ordered_run_ids'] == ['run:atlas-06', 'run:atlas-05', 'run:atlas-08']
    assert minimum['expected_steps'][0]['ordered_run_ids'] == ['run:lyra-04', 'run:lyra-06']
    for case in (top, minimum):
        trace = evaluation.execute_case(case)
        trace['plan']['steps'][0]['query']['include_ties'] = False
        reexecute(case, trace)
        assert not evaluation.grade_contract(case, evaluation.load_bundle(case), trace)['contract_pass']


def test_contract_checks_full_rows_comparison_and_description():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    trace['execution']['results'][0]['result']['runs'][0]['correct'] = 1
    trace['execution']['results'][1]['result']['additional_correct'] = 16
    trace['execution']['results'][2]['result']['notes'] = []
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('returned row values' in f for f in grade['failures'])
    assert any('wrong additional_correct' in f for f in grade['failures'])
    assert any('notes missing or stale' in f for f in grade['failures'])


def test_freeze_captures_sibling_scorer_both_protocols_and_fixture_bytes(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail('Freezing must not construct HTTPModel')
    monkeypatch.setattr(evaluation, 'HTTPModel', forbidden)
    manifest = evaluation.freeze_sources(tmp_path, live=True, repeats=2, workers=4)
    assert manifest['max_model_requests_per_draw'] == 2
    assert manifest['strategies'] == ['v1', 'v2']
    for relative in ('scripts/evaluate_selection.py', 'scripts/evaluate_snapshots.py',
                     'lablens/selection_planning.py', 'evaluation/v0.9/cases.json', 'evaluation/v0.9/rubric.md'):
        assert relative in manifest['source_sha256']
    for source in list((evaluation.ROOT / 'lablens').glob('*.py')) + list((evaluation.ROOT / 'lablens/data').glob('*.json')):
        assert str(source.relative_to(evaluation.ROOT)) in manifest['source_sha256']
    for relative, checksum in manifest['source_sha256'].items():
        raw = (tmp_path / 'frozen-source' / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == checksum
        assert raw == (evaluation.ROOT / relative).read_bytes()
    for version, prompt in [('v1', evaluation.GUIDED_SYSTEM), ('v2', evaluation.GUIDED_SYSTEM_V2)]:
        protocol = manifest['protocols'][version]
        assert (tmp_path / protocol['prompt_file']).read_text() == prompt
        for name in ('prompt', 'schema'):
            assert hashlib.sha256((tmp_path / protocol[name + '_file']).read_bytes()).hexdigest() == protocol[name + '_sha256']
    assert json.loads((tmp_path / 'v1-proposal-schema.json').read_text()) != json.loads((tmp_path / 'v2-proposal-schema.json').read_text())


def proposal_message(case, *, broken=False):
    query = case['expected_steps'][0]['parameters']
    actions = [dict(id='q1', clause_id='c1', operation='query', query=dict(
        metric='accuracy', ranking='maximum' if broken else 'descending', top_k=None,
        include_ties=True, pareto_only=True))]
    pair = case['expected_steps'][1]
    actions += [dict(id='compare1',clause_id='c2',operation='compare',baseline=pair['baseline_id'],candidate=pair['candidate_id']),
                dict(id='describe1',clause_id='c3',operation='describe')]
    return dict(role='assistant', content=None, tool_calls=[dict(id='proposal1',type='function',function=dict(
        name='propose_analysis',arguments=json.dumps(dict(actions=actions))))])


def test_first_attempt_replay_is_zero_provider_calls_and_distinguishes_repair(monkeypatch):
    case = CASES[0]
    instances = []
    class TwoReplies:
        mode = 'fake_live'
        def __init__(self):
            self.calls = 0
            instances.append(self)
        def complete(self, messages, tools):
            self.calls += 1
            return proposal_message(case, broken=self.calls == 1), dict(prompt_tokens=10,completion_tokens=5,estimated_cost=0.01)
    monkeypatch.setattr(evaluation, 'HTTPModel', TwoReplies)
    trace = evaluation.execute_case(case, strategy='v1', live=True)
    assert len(instances) == 1 and instances[0].calls == 2
    assert trace['model_calls'] == 2
    assert trace['contract']['contract_pass']
    assert not trace['first_attempt_contract']['contract_pass']
    assert trace['first_attempt_contract']['additional_provider_requests'] == 0
    row = evaluation.summarize(case, trace, 1)
    assert row['reported_estimated_cost_usd'] == pytest.approx(0.02)
    aggregate = evaluation.aggregate([row])
    assert aggregate['repair_calls'] == aggregate['repaired_contract_success'] == 1
    assert aggregate['first_contract_pass'] == 0 and aggregate['contract_pass'] == 1


def test_provider_configuration_failure_retained_without_reply_or_false_zero_cost(monkeypatch):
    def unavailable():
        raise RuntimeError('No local test configuration')
    monkeypatch.setattr(evaluation, 'HTTPModel', unavailable)
    trace = evaluation.execute_case(CASES[0], live=True)
    assert trace['status'] == 'harness_error' and trace['model_calls'] == 0
    assert not trace['contract']['contract_pass']
    assert trace['first_attempt_contract']['status'] == 'no_model_reply'
    trace.update(model_calls=1, events=[dict(type='model',usage={})])
    row = evaluation.summarize(CASES[0], trace, 1)
    assert row['usage_incomplete'] and row['reported_estimated_cost_usd'] is None
    assert evaluation.aggregate([row])['unknown_usage_draws'] == ['v2-atlas-full-frontier-r1']


def test_declared_frozen_fixture_used_even_if_live_loader_changes(monkeypatch):
    case = CASES[0]
    bundle = evaluation.load_bundle(case)
    def stale(_):
        raise AssertionError('Do not load mutable fixture when frozen input is supplied')
    monkeypatch.setattr(evaluation, 'load_bundle', stale)
    trace = evaluation.execute_case(case, bundle=deepcopy(bundle))
    assert trace['contract']['contract_pass']
