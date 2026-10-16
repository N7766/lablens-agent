import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.guided_planning import Proposal, QuerySpec, compile_proposal, draft_guided
from lablens.model import ModelError
from lablens.plan_checks import clauses, coverage
from lablens.planning import AnalysisPlan, PlanRequest, execute_plan, preview_plan
from lablens.workbench import verify
from test_agent import SequenceModel, call


QUESTION = '筛选准确率不高于95%的最短记录；以run:dpo-09为基线比较该结果；说明词数和时延的边界。'


def proposal():
    return {'actions': [
        dict(id='shortest', clause_id='c1', operation='query', query=dict(
            metric='mean_length', ranking='minimum', top_k=None, include_ties=True, max_accuracy=.95)),
        dict(id='delta', clause_id='c2', operation='compare', baseline='run:dpo-09', candidate='step:shortest'),
        dict(id='scope', clause_id='c3', operation='describe')]}


def test_explicit_ranking_compiles_to_correct_query_and_direction():
    p = compile_proposal(QUESTION, Proposal.model_validate(proposal()))
    assert p.steps[0].query.descending is False and p.steps[0].query.top_k == 1
    request = PlanRequest(question=QUESTION, plan=p)
    request.preview_id = preview_plan(request)['preview_id']
    result = execute_plan(request)
    assert result['results'][0]['result']['ordered_run_ids'] == ['run:dpo-10']
    assert result['results'][1]['result']['additional_correct'] == -1
    assert result['coverage']['all_clauses_accounted_for']
    assert verify(result['receipt'])['step_count'] == 3


@pytest.mark.parametrize('ranking,top,descending,expected_top', [
    ('minimum', None, False, 1), ('maximum', None, True, 1),
    ('ascending', None, False, None), ('descending', 3, True, 3)])
def test_ranking_has_no_implicit_direction(ranking, top, descending, expected_top):
    q = QuerySpec(metric='accuracy', ranking=ranking, top_k=top, include_ties=True).compile()
    assert q['descending'] is descending and q['top_k'] == expected_top


@pytest.mark.parametrize('missing', ['metric', 'ranking', 'top_k', 'include_ties'])
def test_query_requires_explicit_decisions(missing):
    data = proposal(); del data['actions'][0]['query'][missing]
    with pytest.raises(ValueError): Proposal.model_validate(data)


def test_clause_split_preserves_decimal_ids_commas_and_exact_offsets():
    question = '  准确率≤95.2%，比较run:dpo-09；看长度。\nCheck cost? Seed!'
    parts = clauses(question)
    assert [p['text'] for p in parts] == ['准确率≤95.2%，比较run:dpo-09', '看长度', 'Check cost', 'Seed']
    assert all(question[p['start']:p['end']] == p['text'] for p in parts)


@pytest.mark.parametrize('change', ['missing_clause', 'unknown_clause', 'duplicate_id', 'handled_unsupported', 'irrelevant_payload'])
def test_invalid_coverage_or_action_fields_rejected(change):
    data = proposal()
    if change == 'missing_clause': data['actions'].pop()
    if change == 'unknown_clause': data['actions'][0]['clause_id'] = 'c8'
    if change == 'duplicate_id': data['actions'][1]['id'] = 'shortest'
    if change == 'handled_unsupported':
        data['actions'].append(dict(id='no', clause_id='c3', operation='unsupported', reason='missing_data'))
    if change == 'irrelevant_payload': data['actions'][2]['reason'] = 'missing_data'
    with pytest.raises(ValueError): compile_proposal(QUESTION, Proposal.model_validate(data))


def test_missing_clause_repairs_once_with_tool_error_feedback():
    data = proposal(); data['actions'].pop()
    class Capturing(SequenceModel):
        def complete(self, messages, tools):
            self.latest_messages = copy.deepcopy(messages)
            return super().complete(messages, tools)
    model = Capturing([call('propose_analysis', data), call('propose_analysis', proposal(), 'c2')])
    trace = draft_guided(model, QUESTION)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    assert 'Unmapped clauses: c3' in model.latest_messages[-1]['content']
    assert model.latest_messages[-1]['role'] == 'tool'
    assert trace['coverage']['semantic_validation'] == 'not_performed'


def test_invalid_first_proposal_retained_in_events_and_one_call_budget():
    data = proposal(); data['actions'].pop()
    trace = draft_guided(SequenceModel([call('propose_analysis', data)]), QUESTION, max_attempts=1)
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    assert json.loads(trace['events'][0]['message']['tool_calls'][0]['function']['arguments']) == data


def test_stalled_retry_does_not_trigger_third_request():
    data = proposal(); data['actions'].pop()
    trace = draft_guided(SequenceModel([call('propose_analysis', data), call('propose_analysis', data, 'c2')]), QUESTION)
    assert trace['status'] == 'validation_stalled' and trace['model_calls'] == 2


def test_provider_failure_is_not_retried():
    class Timeout:
        mode = 'test_timeout'
        def complete(self, messages, tools): raise ModelError('timeout')
    trace = draft_guided(Timeout(), QUESTION)
    assert trace['status'] == 'model_error' and trace['model_calls'] == 1 and trace['usage_incomplete']


def test_no_stale_plan_after_failed_replacement():
    bad = proposal(); bad['actions'][1]['candidate'] = 'run:unknown'
    trace = draft_guided(SequenceModel([call('propose_analysis', bad), call('propose_analysis', {'actions': []}, 'c2')]), QUESTION)
    assert trace['status'] == 'invalid_plan' and 'plan' not in trace


def test_earlier_query_action_ids_are_unambiguous_references():
    data = proposal(); data['actions'][1]['candidate'] = 'shortest'
    plan = compile_proposal(QUESTION, Proposal.model_validate(data))
    assert plan.steps[1].candidate.query_step == 'shortest'
    for reference in ('unknown', 'scope', 'delta'):
        data['actions'][1]['candidate'] = reference
        with pytest.raises(ValueError, match='earlier query'):
            compile_proposal(QUESTION, Proposal.model_validate(data))


def test_timeout_after_bad_candidate_cannot_return_stale_plan():
    bad = proposal(); bad['actions'][1]['candidate'] = 'run:unknown'
    class ThenTimeout(SequenceModel):
        count = 0
        def complete(self, messages, tools):
            self.count += 1
            if self.count == 2: raise ModelError('timeout')
            return super().complete(messages, tools)
    trace = draft_guided(ThenTimeout([call('propose_analysis', bad)]), QUESTION)
    assert trace['status'] == 'model_error' and 'plan' not in trace
    assert trace['model_calls'] == 2 and trace['usage_incomplete']


def test_malformed_protocol_not_repaired_or_crashed():
    for message in [{}, {'tool_calls': [None]}, {'tool_calls': [{'id': '', 'function': {}}]}]:
        trace = draft_guided(SequenceModel([message]), QUESTION)
        assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1


@pytest.mark.parametrize('budget', [0, 3, True])
def test_invalid_budget_rejected(budget):
    with pytest.raises(ValueError): draft_guided(SequenceModel([]), QUESTION, max_attempts=budget)


def test_partial_quotes_do_not_claim_full_clause_coverage():
    plan = AnalysisPlan.model_validate({'steps': [{'id': 'q', 'kind': 'query', 'request_excerpt': '查询最高', 'query': {}}]})
    report = coverage('查询最高准确率并解释统计显著性', plan)
    assert not report['all_clauses_accounted_for']
    assert report['clauses'][0]['partial_quote_step_ids'] == ['q']


def test_current_receipt_verifier_preserves_all_v05_plan_receipts():
    paths = Path('docs/evaluations/v0.5/holdout').glob('planner-*.json')
    count = 0
    for path in paths:
        trace = json.loads(path.read_text())
        if 'execution' in trace:
            assert verify(trace['execution']['receipt'])['status'] == 'verified'
            count += 1
    assert count == 6


def test_api_draft_uses_guided_planner_and_returns_coverage(monkeypatch):
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: SequenceModel([call('propose_analysis', proposal())]))
    response = TestClient(app).post('/api/plan/draft', json={'question': QUESTION})
    assert response.status_code == 200
    assert response.json()['status'] == 'needs_review'
    assert response.json()['coverage']['all_clauses_accounted_for']


def test_clause_mapping_does_not_pretend_to_prove_intent():
    data = proposal(); data['actions'][0]['query']['ranking'] = 'maximum'
    plan = compile_proposal(QUESTION, Proposal.model_validate(data))
    assert coverage(QUESTION, plan)['all_clauses_accounted_for']
    assert plan.steps[0].query.descending is True  # Known semantic error, correctly NOT claimed verified.
