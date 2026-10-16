"""Public task-check integration, manual review and historical receipt boundaries."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from lablens.api import app
from test_agent import SequenceModel, call

ROOT = Path(__file__).resolve().parents[1]
QUESTION = '列出所有记录；以run:sample-b为基线比较run:sample-e的正确题数差；说明长度单位与数据来源。'


def proposal(reverse=False):
    return {'actions': [
        dict(id='q1', clause_id='c1', operation='query', query=dict(
            order_by='accuracy', direction='descending', selection='all', top_k=None, include_ties=True)),
        dict(id='cmp', clause_id='c2', operation='compare',
             baseline='run:sample-e' if reverse else 'run:sample-b',
             candidate='run:sample-b' if reverse else 'run:sample-e'),
        dict(id='info', clause_id='c3', operation='describe')]}


def context(client):
    bundle = json.loads((ROOT / 'lablens/data/example-bundle.json').read_text())
    snapshot = client.post('/api/plan/context', json={'dataset': bundle}).json()['snapshot_id']
    return bundle, snapshot


def test_public_draft_checks_pair_direction_and_required_describe(monkeypatch):
    model = SequenceModel([call('propose_analysis', proposal(True)), call('propose_analysis', proposal())])
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: model)
    client = TestClient(app)
    bundle, snapshot = context(client)
    response = client.post('/api/plan/draft', json=dict(question=QUESTION, dataset=bundle,
                                                      approved_snapshot_id=snapshot))
    assert response.status_code == 200
    trace = response.json()
    assert trace['task_rules_enabled'] is True and trace['model_calls'] == 2
    assert trace['status'] == 'needs_review' and 'execution' not in trace
    checks = [e['report'] for e in trace['events'] if e['type'] == 'task_check']
    assert len(checks) == 2 and checks[0]['has_conflicts'] and not checks[1]['has_conflicts']
    assert [u['state'] for u in trace['preview']['task_checks']['units']] == ['matched', 'matched']
    result = client.post('/api/plan/execute', json=dict(question=QUESTION, dataset=bundle,
        plan=trace['plan'], preview_id=trace['preview']['preview_id'])).json()
    assert result['results'][1]['result']['additional_correct'] == -15
    assert not result['task_checks']['has_conflicts']
    assert client.post('/api/receipt/verify', json=result['receipt']).json()['status'] == 'verified'


def test_manual_conflict_remains_visible_and_receipt_does_not_claim_intent():
    client = TestClient(app)
    bundle, _ = context(client)
    question = '以run:sample-b为基线比较run:sample-e的正确题数差；说明长度单位与数据来源。'
    plan = dict(steps=[
        dict(id='cmp', kind='compare', request_excerpt=question.split('；')[0],
             baseline=dict(run_id='run:sample-e'), candidate=dict(run_id='run:sample-b')),
        dict(id='info', kind='describe', request_excerpt='说明长度单位与数据来源')], unhandled=[])
    request = dict(question=question, dataset=bundle, plan=plan)
    preview = client.post('/api/plan/preview', json=request).json()
    assert preview['task_checks']['has_conflicts']
    request['preview_id'] = preview['preview_id']
    result = client.post('/api/plan/execute', json=request).json()
    assert result['task_checks']['has_conflicts'] and result['status'] == 'completed'
    assert result['results'][0]['result']['additional_correct'] == 15
    assert result['intent_validation'] == 'not_performed'
    assert client.post('/api/receipt/verify', json=result['receipt']).json()['status'] == 'verified'


def test_v09_holdout_receipts_remain_recomputable_even_when_task_failed():
    client = TestClient(app)
    count, failed = 0, 0
    for path in (ROOT / 'docs/evaluations/v0.9/holdout').glob('v*-r*.json'):
        trace = json.loads(path.read_text())
        receipt = trace.get('execution', {}).get('receipt')
        if receipt:
            assert client.post('/api/receipt/verify', json=receipt).json()['status'] == 'verified'
            count += 1
            failed += not trace['contract']['contract_pass']
    assert (count, failed) == (28, 2)
