"""Public default protocol and unchanged execution/receipt boundary."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from lablens.api import app
from lablens.workbench import verify
from test_agent import SequenceModel, call

ROOT = Path(__file__).resolve().parents[1]


def test_api_uses_explicit_all_selection_on_imported_pareto(monkeypatch):
    bundle = json.loads((ROOT / 'lablens/data/example-bundle.json').read_text())
    proposal = {'actions': [dict(id='q1', clause_id='c1', operation='query', query=dict(
        order_by='accuracy', direction='descending', selection='all', top_k=None,
        include_ties=True, pareto_only=True))]}
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: SequenceModel([call('propose_analysis', proposal)]))
    client = TestClient(app)
    snapshot = client.post('/api/plan/context', json={'dataset': bundle}).json()['snapshot_id']
    question = '列出完整Pareto集合'
    response = client.post('/api/plan/draft', json=dict(question=question, dataset=bundle, approved_snapshot_id=snapshot))
    assert response.status_code == 200
    trace = response.json()
    assert trace['proposal_version'] == 'v2' and trace['status'] == 'needs_review'
    assert trace['plan']['steps'][0]['query']['top_k'] is None
    assert 'execution' not in trace
    result = client.post('/api/plan/execute', json=dict(question=question, plan=trace['plan'],
        dataset=bundle, preview_id=trace['preview']['preview_id'])).json()
    assert result['results'][0]['result']['ordered_run_ids'] == ['run:sample-b', 'run:sample-d', 'run:sample-e']
    assert client.post('/api/receipt/verify', json=result['receipt']).json()['status'] == 'verified'


def test_v08_receipts_remain_compatible_with_new_protocol_default():
    count = 0
    for path in (ROOT / 'docs/evaluations/v0.8/holdout').glob('*-r*.json'):
        trace = json.loads(path.read_text())
        receipt = trace.get('execution', {}).get('receipt')
        if receipt:
            assert verify(receipt)['status'] == 'verified', path.name
            count += 1
    assert count == 8
