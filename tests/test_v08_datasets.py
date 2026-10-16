"""Request-local planner datasets, disclosure binding, and unit-aware checks."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.datasets import DatasetBundle
from lablens.guided_planning import draft_guided
from lablens.planning import PlanRequest, execute_plan, preview_plan
from lablens.tools import ToolBox
from lablens.workbench import verify
from test_agent import SequenceModel, call


QUESTION = '列出准确率最高的所有并列记录'
ROOT = Path(__file__).resolve().parents[1]


def bundle(variant='a', unit='whitespace_words'):
    """Shared IDs deliberately have different winners across two snapshots."""
    counts = [7, 9, 8] if variant == 'a' else [9, 6, 8]
    runs = []
    for index, correct in enumerate(counts):
        runs.append(dict(
            id=f'run:fixture-{index}', label=f'Synthetic fixture {index}',
            project='v08-isolation', dataset='synthetic-arithmetic', split_id='holdout-10',
            num_samples=10, correct=correct, accuracy=correct / 10,
            mean_length=float(10 + index * 5), length_unit=unit,
            tokenizer='fixture-tokenizer-v1' if unit == 'tokenizer_tokens' else None,
            model_config_details={'model': 'none; synthetic fixture'},
            used_for_checkpoint_selection=False, source_path=f'fixture-{index}.json',
            source_sha256=str(index) * 64, synthetic=True))
    return dict(runs=runs, notes=[dict(
        id='note:custom-scope', title='Synthetic scope',
        text=f'Variant {variant}; fabricated data for isolation tests, not measured model performance.',
        source='synthetic-fixtures')])


def proposal(query=None):
    return {'actions': [dict(id='selection', clause_id='c1', operation='query', query=query or dict(
        metric='accuracy', ranking='maximum', top_k=None, include_ties=True))]}


def api_proposal():
    data = proposal()
    data['actions'][0]['query'] = dict(order_by='accuracy', direction='descending',
        selection='extreme', top_k=None, include_ties=True)
    return data


class CapturingModel(SequenceModel):
    def complete(self, messages, tools):
        self.messages = copy.deepcopy(messages)
        return super().complete(messages, tools)


def finish(trace):
    return execute_plan(PlanRequest(question=trace['question'], plan=trace['plan'],
                                  dataset=trace['input_dataset'], preview_id=trace['preview']['preview_id']))


def test_guided_prompt_preview_execution_and_receipt_share_custom_snapshot():
    data = DatasetBundle.model_validate(bundle())
    expected = ToolBox(bundle=data.model_dump())
    model = CapturingModel([call('propose_analysis', proposal())])
    trace = draft_guided(model, QUESTION, dataset=data, check_request_rules=True)
    context = json.loads(model.messages[1]['content'])
    assert context['runs'] == expected.runs
    assert context['coverage']['notes'] == expected.notes
    assert all(not row['id'].startswith('run:dpo') for row in context['runs'])
    assert trace['input_dataset'] == {'runs': expected.runs, 'notes': expected.notes}
    assert trace['snapshot_id'] == trace['preview']['snapshot_id'] == expected.snapshot_id
    assert trace['status'] == 'needs_review' and 'execution' not in trace
    result = finish(trace)
    assert result['results'][0]['result']['ordered_run_ids'] == ['run:fixture-1']
    assert result['receipt']['dataset'] == trace['input_dataset']
    assert verify(result['receipt'])['status'] == 'verified'


def test_concurrent_same_ids_do_not_share_snapshot_or_change_builtin():
    original = ToolBox().snapshot_id

    def run(variant):
        model = CapturingModel([call('propose_analysis', proposal())])
        trace = draft_guided(model, QUESTION, dataset=DatasetBundle.model_validate(bundle(variant)))
        return trace['snapshot_id'], finish(trace)['results'][0]['result']['ordered_run_ids']

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, ['a', 'b']))
    assert a[0] != b[0]
    assert a[1] == ['run:fixture-1'] and b[1] == ['run:fixture-0']
    assert ToolBox().snapshot_id == original


def test_mutating_callers_bundle_during_model_request_cannot_change_prepared_input():
    data = DatasetBundle.model_validate(bundle())
    original = ToolBox(bundle=data.model_dump())

    class MutatingModel(CapturingModel):
        def complete(self, messages, tools):
            data.runs[0].correct = 10
            data.runs[0].accuracy = 1.0
            data.notes[0].text = 'Changed after the model request began.'
            return super().complete(messages, tools)

    model = MutatingModel([call('propose_analysis', proposal())])
    trace = draft_guided(model, QUESTION, dataset=data)
    assert trace['snapshot_id'] == original.snapshot_id
    assert trace['input_dataset'] == {'runs': original.runs, 'notes': original.notes}
    assert json.loads(model.messages[1]['content'])['runs'] == original.runs
    assert finish(trace)['results'][0]['result']['ordered_run_ids'] == ['run:fixture-1']


def test_custom_draft_rejects_builtin_run_reference_even_when_builtin_contains_it():
    actions = {'actions': [dict(id='compare', clause_id='c1', operation='compare',
                               baseline='run:sft', candidate='run:dpo-best')]}
    trace = draft_guided(SequenceModel([call('propose_analysis', actions)]),
                        '比较run:sft与run:dpo-best', max_attempts=1,
                        dataset=DatasetBundle.model_validate(bundle()))
    assert trace['status'] == 'invalid_plan' and 'preview' not in trace
    assert 'Unknown run ID' in trace['error']


def forbidden_model():
    raise AssertionError('HTTPModel must not be constructed before consent and size checks')


@pytest.mark.parametrize('approval', [None, 'snapshot:wrong'])
def test_api_rejects_missing_or_stale_custom_data_approval_before_model_construction(monkeypatch, approval):
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    payload = dict(question=QUESTION, dataset=bundle())
    if approval is not None:
        payload['approved_snapshot_id'] = approval
    response = TestClient(app).post('/api/plan/draft', json=payload)
    assert response.status_code == 422


def test_context_inspection_is_local_and_approval_is_bound_to_normalized_snapshot(monkeypatch):
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    client = TestClient(app)
    data = bundle()
    response = client.post('/api/plan/context', json={'dataset': data})
    assert response.status_code == 200
    info = response.json()
    assert info['model_input_allowed'] and info['model_upload_required']
    assert info['snapshot_id'] == ToolBox(bundle=data).snapshot_id
    assert info['max_context_bytes'] == 128 * 1024
    assert 0 < info['context_bytes'] < info['max_context_bytes']
    data['runs'].reverse()
    assert client.post('/api/plan/context', json={'dataset': data}).json()['snapshot_id'] == info['snapshot_id']
    model = CapturingModel([call('propose_analysis', api_proposal())])
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: model)
    draft = client.post('/api/plan/draft', json=dict(
        question=QUESTION, dataset=data, approved_snapshot_id=info['snapshot_id']))
    assert draft.status_code == 200 and draft.json()['status'] == 'needs_review'
    assert draft.json()['snapshot_id'] == info['snapshot_id']


@pytest.mark.parametrize('change', ['numbers', 'notes'])
def test_approval_and_preview_reject_changed_snapshot_even_with_same_ids(monkeypatch, change):
    client = TestClient(app)
    data = bundle()
    approval = client.post('/api/plan/context', json={'dataset': data}).json()['snapshot_id']
    plan = dict(steps=[dict(id='selection', kind='query', request_excerpt=QUESTION, query={'top_k': 1})])
    request = PlanRequest(question=QUESTION, plan=plan, dataset=data)
    old_preview = preview_plan(request)['preview_id']
    if change == 'numbers':
        data['runs'][0]['mean_length'] += 1
    else:
        data['notes'][0]['text'] += ' Additional scope note.'
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    response = client.post('/api/plan/draft', json=dict(
        question=QUESTION, dataset=data, approved_snapshot_id=approval))
    assert response.status_code == 422
    request = PlanRequest(question=QUESTION, plan=plan, dataset=data, preview_id=old_preview)
    with pytest.raises(ValueError, match='stale'):
        execute_plan(request)


def test_context_limit_counts_utf8_bytes_and_fails_before_model_construction(monkeypatch):
    client = TestClient(app)
    data = bundle()
    data['runs'][0]['model_config_details']['description'] = '汉' * 45000
    # Valid interchange data may exceed the much smaller planner context budget.
    assert len(json.dumps(data, ensure_ascii=False)) < 128 * 1024
    inspect = client.post('/api/dataset/inspect', json=data)
    assert inspect.status_code == 200
    context = client.post('/api/plan/context', json={'dataset': data})
    assert context.status_code == 200
    info = context.json()
    assert info['context_bytes'] > 128 * 1024 and not info['model_input_allowed']
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    response = client.post('/api/plan/draft', json=dict(
        question=QUESTION, dataset=data, approved_snapshot_id=info['snapshot_id']))
    assert response.status_code == 422
    with pytest.raises(ValueError):
        draft_guided(SequenceModel([]), QUESTION, dataset=DatasetBundle.model_validate(data))


def test_context_limit_includes_exact_boundary_without_oversize_retry(monkeypatch):
    client = TestClient(app)
    data = bundle()
    data['runs'][0]['model_config_details']['padding'] = ''
    initial = client.post('/api/plan/context', json={'dataset': data}).json()
    data['runs'][0]['model_config_details']['padding'] = 'x' * (initial['max_context_bytes'] - initial['context_bytes'])
    exact = client.post('/api/plan/context', json={'dataset': data}).json()
    assert exact['context_bytes'] == exact['max_context_bytes'] and exact['model_input_allowed']
    data['runs'][0]['model_config_details']['padding'] += 'x'
    over = client.post('/api/plan/context', json={'dataset': data}).json()
    assert over['context_bytes'] == over['max_context_bytes'] + 1 and not over['model_input_allowed']
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    assert client.post('/api/plan/draft', json=dict(
        question=QUESTION, dataset=data, approved_snapshot_id=over['snapshot_id'])).status_code == 422


@pytest.mark.parametrize('invalid', ['duplicate_run', 'duplicate_note', 'note_too_long', 'inconsistent_accuracy'])
def test_invalid_import_never_reaches_model(monkeypatch, invalid):
    data = bundle()
    if invalid == 'duplicate_run':
        data['runs'].append(copy.deepcopy(data['runs'][0]))
    elif invalid == 'duplicate_note':
        data['notes'].append(copy.deepcopy(data['notes'][0]))
    elif invalid == 'note_too_long':
        data['notes'][0]['text'] = 'x' * 6001
    else:
        data['runs'][0]['accuracy'] = .1
    monkeypatch.setattr('lablens.api.HTTPModel', forbidden_model)
    client = TestClient(app)
    assert client.post('/api/plan/context', json={'dataset': data}).status_code == 422
    assert client.post('/api/plan/draft', json=dict(
        question=QUESTION, dataset=data, approved_snapshot_id='snapshot:fake')).status_code == 422


def test_builtin_draft_remains_compatible_without_upload_approval(monkeypatch):
    model = CapturingModel([call('propose_analysis', api_proposal())])
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: model)
    client = TestClient(app)
    info = client.post('/api/plan/context', json={}).json()
    assert not info['model_upload_required'] and info['model_input_allowed']
    response = client.post('/api/plan/draft', json={'question': QUESTION})
    assert response.status_code == 200 and response.json()['status'] == 'needs_review'
    assert response.json()['snapshot_id'] == ToolBox().snapshot_id


def test_word_threshold_abstains_on_token_dataset_in_draft_preview_and_execution():
    question = '列出平均长度严格小于20词的所有记录'
    query = dict(metric='accuracy', ranking='descending', top_k=None, include_ties=True, max_length=20)
    trace = draft_guided(SequenceModel([call('propose_analysis', proposal(query))]), question,
                        dataset=DatasetBundle.model_validate(bundle(unit='tokenizer_tokens')),
                        check_request_rules=True)
    assert trace['status'] == 'needs_review'
    for report in [trace['request_checks'], trace['preview']['request_checks'], finish(trace)['request_checks']]:
        assert report['status'] == 'not_assessed' and report['units'] == []
        assert report['unassessed'] and not report['has_conflicts']


def test_unit_neutral_extreme_remains_checkable_on_token_dataset():
    question = '列出平均长度最短的所有并列记录'
    query = dict(metric='mean_length', ranking='minimum', top_k=None, include_ties=True)
    trace = draft_guided(SequenceModel([call('propose_analysis', proposal(query))]), question,
                        dataset=DatasetBundle.model_validate(bundle(unit='tokenizer_tokens')),
                        check_request_rules=True)
    assert trace['request_checks']['status'] == 'checked'
    assert trace['preview']['request_checks']['status'] == 'checked'
    assert finish(trace)['results'][0]['result']['ordered_run_ids'] == ['run:fixture-0']


def test_all_v07_holdout_receipts_still_recompute():
    receipts = []
    for path in (ROOT / 'docs/evaluations/v0.7/holdout').glob('*.json'):
        content = json.loads(path.read_text())
        if isinstance(content, dict):
            receipt = content.get('execution', {}).get('receipt')
            if receipt:
                receipts.append((path.name, receipt))
    assert len(receipts) == 22
    for name, receipt in receipts:
        assert verify(receipt)['status'] == 'verified', name
