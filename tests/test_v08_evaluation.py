import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from lablens.datasets import DatasetBundle
from lablens.plan_checks import clauses
from lablens.planning import PlanRequest, execute_plan, preview_plan

spec = importlib.util.spec_from_file_location('evaluate_snapshots', Path(__file__).resolve().parents[1] / 'scripts/evaluate_snapshots.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


CASES = evaluation.load_cases()


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_predeclared_oracles_match_runtime_without_calling_model(case, monkeypatch):
    def forbidden():
        pytest.fail('Offline oracle must not construct HTTPModel')
    monkeypatch.setattr(evaluation, 'HTTPModel', forbidden)
    trace = evaluation.execute_case(case)
    assert trace['contract']['contract_pass'], trace['contract']['failures']
    assert trace['receipt_check']['verified']
    assert trace['model_calls'] == 0 and trace['events'] == []
    assert len(trace['execution']['results']) == 3
    assert [s['request_excerpt'] for s in case['expected_steps']] == [c['text'] for c in clauses(case['question'])]


def test_sources_are_explicit_synthetic_bytes_and_contract_deltas_are_independent():
    for case in CASES:
        bundle = evaluation.load_bundle(case)
        DatasetBundle.model_validate(bundle).normalized()
        assert evaluation.snapshot_digest(bundle) == case['expected_snapshot_id']
        index = {run['id']: run for run in bundle['runs']}
        for run in bundle['runs']:
            raw = (evaluation.ROOT / run['source_path']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == run['source_sha256']
            source = json.loads(raw)
            assert source['purpose'].startswith('Hand-authored synthetic engineering fixture')
            assert run['synthetic'] is True
            described = next(row for row in source['rows'] if row['id'] == run['id'])
            assert (run['correct'], run['mean_length']) == (described['correct'], described['mean_length'])
        pair = case['expected_steps'][1]
        a, b = index[pair['baseline_id']], index[pair['candidate_id']]
        assert pair['additional_correct'] == b['correct'] - a['correct']
        # Independent fractions rather than runtime comparison or output rendering.
        assert pair['accuracy_delta_percentage_points'] == pytest.approx(
            100 * (b['correct'] - a['correct']) / a['num_samples'])
        assert pair['length_reduction_percent'] == pytest.approx(
            100 * (a['mean_length'] - b['mean_length']) / a['mean_length'])


def test_same_id_fixtures_change_ranking_ties_and_pareto_members():
    a, b = evaluation.load_bundle(CASES[0]), evaluation.load_bundle(CASES[2])
    assert [r['id'] for r in a['runs']] == [r['id'] for r in b['runs']]
    assert all(x['correct'] != y['correct'] or x['mean_length'] != y['mean_length'] for x, y in zip(a['runs'], b['runs']))
    assert CASES[0]['question'] == CASES[2]['question']
    assert CASES[1]['question'] == CASES[3]['question']
    assert CASES[0]['expected_steps'][0]['ordered_run_ids'] == ['run:sample-b', 'run:sample-c', 'run:sample-d']
    assert CASES[2]['expected_steps'][0]['ordered_run_ids'] == ['run:sample-d', 'run:sample-e', 'run:sample-f']
    assert set(CASES[1]['expected_steps'][0]['ordered_run_ids']) == {'run:sample-b', 'run:sample-d', 'run:sample-e'}
    assert set(CASES[3]['expected_steps'][0]['ordered_run_ids']) == {'run:sample-a', 'run:sample-b', 'run:sample-d', 'run:sample-f'}
    sample = json.loads((evaluation.ROOT / 'lablens/data/example-bundle.json').read_text())
    assert sample == a


def test_contract_rejects_other_snapshot_even_when_ids_collide():
    old = evaluation.execute_case(CASES[0])
    grade = evaluation.grade_contract(CASES[2], evaluation.load_bundle(CASES[2]), old)
    assert not grade['contract_pass']
    assert 'trace uses wrong snapshot' in grade['failures']
    assert any('row values' in issue for issue in grade['failures'])


def test_contract_rejects_stale_rows_even_with_expected_fingerprint_and_ids():
    case = CASES[2]
    trace = evaluation.execute_case(case)
    old_rows = {r['id']: r for r in evaluation.load_bundle(CASES[0])['runs']}
    result = trace['execution']['results'][0]['result']
    result['runs'] = [old_rows[rid] for rid in result['ordered_run_ids']]
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('returned row values' in issue for issue in grade['failures'])


@pytest.mark.parametrize('parameter,wrong', [('top_k', 1), ('min_accuracy', 0.99), ('pareto_only', True)])
def test_wrong_parameters_fail_even_when_empty_result_is_unchanged(parameter, wrong):
    case = CASES[-1]
    bundle = evaluation.load_bundle(case)
    trace = evaluation.execute_case(case)
    trace['plan']['steps'][0]['query'][parameter] = wrong
    request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle)
    trace['preview'] = preview_plan(request)
    request.preview_id = trace['preview']['preview_id']
    trace['execution'] = execute_plan(request)
    assert trace['execution']['results'][0]['result']['ordered_run_ids'] == []
    assert evaluation.receipt_check(trace)['verified']
    grade = evaluation.grade_contract(case, bundle, trace)
    assert not grade['contract_pass']
    assert any('plan parameter ' + parameter in reason for reason in grade['failures'])


def test_partial_plan_and_missing_description_do_not_pass():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    trace['plan']['steps'].pop()
    trace['execution']['results'].pop()
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('describe' in reason for reason in grade['failures'])


def test_wrong_comparison_direction_or_missing_current_notes_fails():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    trace['plan']['steps'][1]['baseline'], trace['plan']['steps'][1]['candidate'] = (
        trace['plan']['steps'][1]['candidate'], trace['plan']['steps'][1]['baseline'])
    trace['execution']['results'][2]['result']['notes'] = []
    grade = evaluation.grade_contract(case, evaluation.load_bundle(case), trace)
    assert not grade['contract_pass']
    assert any('wrong explicit baseline' in reason for reason in grade['failures'])
    assert any('notes missing or stale' in reason for reason in grade['failures'])


def test_unbounded_query_contract_does_not_invent_sort_requirement():
    case = CASES[1]
    bundle = evaluation.load_bundle(case)
    trace = evaluation.execute_case(case)
    trace['plan']['steps'][0]['query'].update(order_by='mean_length', descending=False)
    request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle)
    trace['preview'] = preview_plan(request)
    request.preview_id = trace['preview']['preview_id']
    trace['execution'] = execute_plan(request)
    assert evaluation.grade_contract(case, bundle, trace)['contract_pass']


def test_freeze_persists_all_runtime_data_and_contract_bytes_before_live(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail('Freezing sources must not instantiate HTTPModel')
    monkeypatch.setattr(evaluation, 'HTTPModel', forbidden)
    manifest = evaluation.freeze_sources(tmp_path, live=True, repeats=2, workers=4)
    assert (tmp_path / 'manifest.json').exists()
    expected = list((evaluation.ROOT / 'lablens').glob('*.py')) + list((evaluation.ROOT / 'lablens/data').glob('*.json'))
    for source in expected:
        relative = str(source.relative_to(evaluation.ROOT))
        assert relative in manifest['source_sha256']
    for relative, checksum in manifest['source_sha256'].items():
        raw = (tmp_path / 'frozen-source' / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == checksum
        assert raw == (evaluation.ROOT / relative).read_bytes()
    assert 'evaluation/v0.8/cases.json' in manifest['source_sha256']
    assert 'evaluation/v0.8/rubric.md' in manifest['source_sha256']


def test_each_configuration_failure_is_recorded_without_model_calls(monkeypatch):
    def failed():
        raise RuntimeError('No local test configuration')
    monkeypatch.setattr(evaluation, 'HTTPModel', failed)
    traces = [evaluation.execute_case(case, live=True) for case in CASES]
    assert len(traces) == 6
    assert all(trace['status'] == 'harness_error' and trace['model_calls'] == 0 for trace in traces)
    assert all(not trace['contract']['contract_pass'] for trace in traces)


def test_unknown_usage_is_not_reported_as_zero_cost():
    case = CASES[0]
    trace = evaluation.execute_case(case)
    trace.update(model_calls=1, events=[dict(type='model', usage={})])
    row = evaluation.summarize(case, trace, 1)
    assert row['usage_incomplete'] is True and row['reported_estimated_cost_usd'] is None
