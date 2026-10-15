import copy
import json

import pytest
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.model import ModelError
from lablens.planning import PlanRequest, draft_plan, execute_plan, preview_plan
from lablens.tools import ToolBox
from lablens.workbench import receipt_digest, verify
from test_agent import SequenceModel, call


QUESTION = '找出准确率最高的所有并列记录；以SFT为基线比较该唯一结果；说明验证集和显著性边界。'


def request():
    return PlanRequest(question=QUESTION, plan={'steps': [
        {'id': 'best', 'kind': 'query', 'request_excerpt': '找出准确率最高的所有并列记录',
         'query': {'top_k': 1}},
        {'id': 'delta', 'kind': 'compare', 'request_excerpt': '以SFT为基线比较该唯一结果',
         'baseline': {'run_id': 'run:sft'}, 'candidate': {'query_step': 'best'}},
        {'id': 'limits', 'kind': 'describe', 'request_excerpt': '说明验证集和显著性边界'}]})



def test_plan_resolves_unique_selection_and_recomputes_all_steps():
    req = request()
    p = preview_plan(req)
    assert 'run:sft → 候选 run:dpo-best' in p['steps'][1]['conditions'][0]
    req.preview_id = p['preview_id']
    result = execute_plan(req)
    assert result['model_calls'] == 0 and result['status'] == 'completed'
    assert result['executed_step_ids'] == ['best', 'delta', 'limits']
    assert result['results'][1]['result']['additional_correct'] == 3
    assert verify(result['receipt'])['step_count'] == 3
    assert result['intent_validation'] == 'not_performed'


@pytest.mark.parametrize('query', [
    {'top_k': 1, 'order_by': 'correct', 'descending': False}, {'min_correct': 999}])
def test_comparison_does_not_silently_pick_ties_or_empty(query):
    data = request().model_dump()
    data['plan']['steps'][0]['query'] = query
    with pytest.raises(ValueError, match='requires exactly one'):
        preview_plan(PlanRequest.model_validate(data))


@pytest.mark.parametrize('change', ['question', 'plan', 'data', 'notes'])
def test_stale_preview_binds_every_relevant_input(change):
    req = request()
    tools = ToolBox()
    data = req.model_dump()
    data['dataset'] = {'runs': copy.deepcopy(tools.runs), 'notes': copy.deepcopy(tools.notes)}
    req = PlanRequest.model_validate(data)
    req.preview_id = preview_plan(req)['preview_id']
    if change == 'question':
        req.question += ' 请仔细检查。'
    elif change == 'plan':
        req.plan.steps[0].query.min_correct = 440
    elif change == 'data':
        req.dataset.runs[0].mean_length += 1
    else:
        req.dataset.notes[0].text += ' Updated.'
    with pytest.raises(ValueError, match='stale'):
        execute_plan(req)


@pytest.mark.parametrize('change', ['quote', 'duplicate_id', 'forward_reference', 'two_refs', 'no_refs'])
def test_invalid_plan_structure_or_references(change):
    data = request().model_dump()
    steps = data['plan']['steps']
    if change == 'quote': steps[0]['request_excerpt'] = 'this was never requested'
    if change == 'duplicate_id': steps[1]['id'] = steps[0]['id']
    if change == 'forward_reference': steps.reverse()
    if change == 'two_refs': steps[1]['candidate']['run_id'] = 'run:dpo-best'
    if change == 'no_refs': steps[1]['candidate'] = {}
    with pytest.raises(ValueError):
        preview_plan(PlanRequest.model_validate(data))


def test_partial_plan_does_not_claim_all_parts_executed():
    req = request()
    data = req.model_dump()
    data['question'] += ' 再计算训练费用。'
    data['plan']['unhandled'] = [{'request_excerpt': '计算训练费用', 'reason': 'missing_data'}]
    req = PlanRequest.model_validate(data)
    req.preview_id = preview_plan(req)['preview_id']
    result = execute_plan(req)
    assert result['status'] == 'partial'
    assert len(result['unhandled']) == 1
    assert len(result['executed_step_ids']) == 3


def test_plan_receipt_rejects_wrong_delta_even_with_rehashed_content():
    req = request(); req.preview_id = preview_plan(req)['preview_id']
    receipt = execute_plan(req)['receipt']
    receipt['results'][1]['result']['additional_correct'] = 999
    receipt['content_sha256'] = receipt_digest({k: v for k, v in receipt.items() if k != 'content_sha256'})
    with pytest.raises(ValueError, match='Recomputed plan'):
        verify(receipt)


def test_plan_receipt_distinguishes_boolean_from_zero_and_survives_json_numbers():
    req = request()
    req.plan.steps[1].candidate.run_id = 'run:sft'
    req.plan.steps[1].candidate.query_step = None
    req.preview_id = preview_plan(req)['preview_id']
    receipt = execute_plan(req)['receipt']
    assert receipt['results'][1]['result']['additional_correct'] == 0
    # JSON browser clients serialize 1.0 as 1, which is semantically the same number.
    portable = json.loads(json.dumps(receipt).replace('1.0,', '1,').replace('0.0,', '0,'))
    assert verify(portable)['status'] == 'verified'
    receipt['results'][1]['result']['additional_correct'] = False
    receipt['content_sha256'] = receipt_digest({k: v for k, v in receipt.items() if k != 'content_sha256'})
    with pytest.raises(ValueError, match='Recomputed plan'):
        verify(receipt)


def test_draft_is_one_call_and_needs_review_before_execution():
    trace = draft_plan(SequenceModel([call('submit_plan', request().plan.model_dump())]), QUESTION)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 1
    assert 'results' not in trace and 'receipt' not in trace
    assert trace['preview']['quote_validation'] == 'exact_substrings_only'


def test_draft_invalid_plan_or_timeout_is_not_retried():
    trace = draft_plan(SequenceModel([call('submit_plan', {'steps': []})]), QUESTION)
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    class Timeout:
        mode = 'test_timeout'
        def complete(self, messages, tools):
            raise ModelError('timeout')
    trace = draft_plan(Timeout(), QUESTION)
    assert trace['status'] == 'model_error' and trace['usage_incomplete']


def test_rejected_reference_draft_remains_editable():
    plan = request().plan.model_dump()
    plan['steps'][0]['query']['top_k'] = None
    trace = draft_plan(SequenceModel([call('submit_plan', plan)]), QUESTION)
    assert trace['status'] == 'invalid_plan'
    assert trace['plan'] == plan and 'preview' not in trace


def test_plan_api_routes_and_tamper_rejection():
    client = TestClient(app)
    data = request().model_dump()
    assert client.post('/api/plan/execute', json=data).status_code == 422
    p = client.post('/api/plan/preview', json=data)
    assert p.status_code == 200
    data['preview_id'] = p.json()['preview_id']
    result = client.post('/api/plan/execute', json=data)
    assert result.status_code == 200
    receipt = result.json()['receipt']
    assert client.post('/api/receipt/verify', json=receipt).json()['step_count'] == 3
    receipt['question'] += 'changed'
    assert client.post('/api/receipt/verify', json=receipt).status_code == 422


def test_plan_uses_custom_data_without_changing_builtin():
    before = ToolBox().snapshot_id
    rows = json.load(open('evaluation/fixtures/synthetic-runs.json'))
    data = request().model_dump()
    data['dataset'] = {'runs': rows, 'notes': []}
    data['plan']['steps'][1]['baseline'] = {'run_id': rows[0]['id']}
    req = PlanRequest.model_validate(data)
    req.preview_id = preview_plan(req)['preview_id']
    result = execute_plan(req)
    assert verify(result['receipt'])['step_count'] == 3
    assert ToolBox().snapshot_id == before
