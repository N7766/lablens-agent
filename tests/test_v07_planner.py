import copy
import json

from fastapi.testclient import TestClient

from lablens.api import app
from lablens.guided_planning import draft_guided
from lablens.model import ModelError
from lablens.planning import PlanRequest, preview_plan, execute_plan
from lablens.workbench import verify
from test_agent import SequenceModel, call

QUESTION = '列出正确题数至少443的所有记录'


def action(top):
    return {'actions': [dict(id='q1', clause_id='c1', operation='query', query=dict(
        metric='correct', ranking='descending', top_k=top, include_ties=True, min_correct=443))]}


def test_rule_conflict_repairs_once_without_changing_model_prompt_or_schema():
    class Capture(SequenceModel):
        def complete(self, messages, tools):
            self.latest = copy.deepcopy(messages)
            return super().complete(messages, tools)
    model = Capture([call('propose_analysis', action(1)), call('propose_analysis', action(None), 'c2')])
    trace = draft_guided(model, QUESTION, check_request_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    assert 'Limited request-rule conflict' in model.latest[-1]['content']
    assert [e['report']['has_conflicts'] for e in trace['events'] if e['type']=='request_check'] == [True, False]
    assert json.loads(trace['events'][0]['message']['tool_calls'][0]['function']['arguments']) == action(1)
    assert trace['request_checks']['status'] == 'checked'


def test_same_initial_model_output_is_valid_in_v06_baseline_but_rejected_by_rules():
    old = draft_guided(SequenceModel([call('propose_analysis', action(1))]), QUESTION)
    new = draft_guided(SequenceModel([call('propose_analysis', action(1))]), QUESTION,
                       max_attempts=1, check_request_rules=True)
    assert old['status'] == 'needs_review' and new['status'] == 'invalid_plan'
    assert new['request_checks']['has_conflicts'] and 'preview' not in new


def test_repeated_conflict_stops_at_two_and_keeps_original_events():
    trace = draft_guided(SequenceModel([call('propose_analysis', action(1)), call('propose_analysis', action(1), 'c2')]),
                        QUESTION, check_request_rules=True)
    assert trace['status'] == 'validation_stalled' and trace['model_calls'] == 2
    assert 'preview' not in trace and trace['request_checks']['has_conflicts']


def test_timeout_after_conflict_does_not_return_stale_check_or_plan():
    class Timeout(SequenceModel):
        def complete(self, messages, tools):
            if len(messages)>2: raise ModelError('timeout')
            return super().complete(messages, tools)
    trace = draft_guided(Timeout([call('propose_analysis', action(1))]), QUESTION, check_request_rules=True)
    assert trace['status'] == 'model_error' and trace['model_calls'] == 2
    assert 'plan' not in trace and 'preview' not in trace and 'request_checks' not in trace
    assert trace['events'][1]['report']['has_conflicts']


def test_manual_plan_diagnostics_are_advisory_and_receipt_stays_recomputable():
    plan = dict(steps=[dict(id='q1',kind='query',request_excerpt=QUESTION,query=dict(min_correct=443,top_k=1))])
    request = PlanRequest(question=QUESTION, plan=plan)
    preview = preview_plan(request)
    assert preview['request_checks']['has_conflicts']
    request.preview_id = preview['preview_id']
    result = execute_plan(request)
    assert result['request_checks']['has_conflicts']
    assert result['results'][0]['result']['ordered_run_ids'] == []
    assert verify(result['receipt'])['status'] == 'verified'


def test_api_default_enables_rules_and_keeps_two_call_budget(monkeypatch):
    def api_action(top):
        data = action(top)
        data['actions'][0]['query'] = dict(order_by='correct', direction='descending',
            selection='all' if top is None else 'top_k', top_k=top, include_ties=True, min_correct=443)
        return data
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: SequenceModel([
        call('propose_analysis',api_action(1)),call('propose_analysis',api_action(None),'c2')]))
    response = TestClient(app).post('/api/plan/draft',json={'question':QUESTION})
    assert response.status_code == 200
    trace=response.json()
    assert trace['proposal_version']=='v2'
    assert trace['request_rules_enabled'] and trace['status']=='needs_review'
    assert trace['model_calls']==2 and trace['request_checks']['status']=='checked'
