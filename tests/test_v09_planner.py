"""Selection v2 fixes proposal ambiguity without silently repairing model output."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lablens.datasets import DatasetBundle
from lablens.guided_planning import (
    GUIDED_SYSTEM, GUIDED_SYSTEM_V2, Proposal, ProposalV2, compile_proposal, draft_guided,
)
from lablens.model import ModelError
from lablens.planning import PlanRequest, execute_plan
from lablens.request_checks import check_requests
from lablens.selection_planning import SelectionQuerySpec, selection_conflict_feedback
from lablens.tools import ToolBox
from lablens.workbench import verify
from test_agent import SequenceModel, call

ROOT = Path(__file__).resolve().parents[1]
QUESTION = '列出完整Pareto集合'


def query(**changes):
    return dict(order_by='accuracy', direction='descending', selection='all', top_k=None,
                include_ties=True, pareto_only=True) | changes


def proposal(**changes):
    return {'actions': [dict(id='q1', clause_id='c1', operation='query', query=query(**changes))]}


class Capture(SequenceModel):
    def __init__(self, sequence):
        super().__init__(sequence)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return super().complete(messages, tools)


@pytest.mark.parametrize('direction,descending', [('ascending', False), ('descending', True)])
@pytest.mark.parametrize('selection,top,compiled_top', [('all', None, None), ('top_k', 2, 2), ('extreme', None, 1)])
def test_selection_and_sorting_are_independent(direction, descending, selection, top, compiled_top):
    spec = SelectionQuerySpec.model_validate(query(direction=direction, selection=selection, top_k=top))
    before = spec.model_dump()
    compiled = spec.compile()
    assert compiled['top_k'] == compiled_top and compiled['descending'] is descending
    assert compiled['order_by'] == 'accuracy' and compiled['pareto_only'] is True
    assert spec.model_dump() == before


@pytest.mark.parametrize('changes', [
    {'selection': 'all', 'top_k': 1}, {'selection': 'extreme', 'top_k': 1},
    {'selection': 'top_k', 'top_k': None}, {'selection': 'top_k', 'top_k': 0},
    {'selection': 'top_k', 'top_k': 101}, {'selection': 'top_k', 'top_k': True},
    {'selection': 'top_k', 'top_k': 1.0}, {'selection': 'top_k', 'top_k': '2'},
    {'direction': 'maximum'}, {'selection': 'maximum'}, {'descending': True},
])
def test_contradictory_and_legacy_selection_fields_are_rejected(changes):
    with pytest.raises(ValidationError):
        SelectionQuerySpec.model_validate(query(**changes))


def test_nullable_top_k_is_required_in_wire_schema_and_payload():
    schema = SelectionQuerySpec.model_json_schema()
    assert 'top_k' in schema['required']
    payload = query()
    del payload['top_k']
    with pytest.raises(ValidationError, match='top_k'):
        SelectionQuerySpec.model_validate(payload)


def test_complete_pareto_keeps_every_frontier_row_and_receipt_recomputes():
    bundle = DatasetBundle.model_validate_json((ROOT / 'evaluation/v0.8/fixtures/snapshot-a.json').read_text())
    model = Capture([call('propose_analysis', proposal())])
    trace = draft_guided(model, QUESTION, dataset=bundle, proposal_version='v2', check_request_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 1
    assert trace['proposal_version'] == 'v2' and trace['request_checks']['status'] == 'checked'
    result = execute_plan(PlanRequest(question=QUESTION, plan=trace['plan'], dataset=trace['input_dataset'],
                                      preview_id=trace['preview']['preview_id']))
    assert result['results'][0]['result']['ordered_run_ids'] == ['run:sample-b', 'run:sample-d', 'run:sample-e']
    assert verify(result['receipt'])['status'] == 'verified'


@pytest.mark.parametrize('selection,top,expected', [('top_k', 2, ['run:sample-b', 'run:sample-d']),
                                                 ('extreme', None, ['run:sample-b'])])
def test_explicit_selection_within_pareto_is_supported(selection, top, expected):
    bundle = json.loads((ROOT / 'evaluation/v0.8/fixtures/snapshot-a.json').read_text())
    spec = SelectionQuerySpec.model_validate(query(selection=selection, top_k=top))
    assert ToolBox(bundle=bundle).execute('query_runs', spec.compile())['ordered_run_ids'] == expected


def test_wrong_extreme_is_visible_and_repaired_only_by_a_second_model_reply():
    wrong, replacement = proposal(selection='extreme'), proposal()
    model = Capture([call('propose_analysis', wrong), call('propose_analysis', replacement, 'c2')])
    trace = draft_guided(model, QUESTION, proposal_version='v2', check_request_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    reports = [event['report'] for event in trace['events'] if event['type'] == 'request_check']
    assert [report['has_conflicts'] for report in reports] == [True, False]
    assert reports[0]['units'][0]['actual'][0]['query']['top_k'] == 1
    assert json.loads(trace['events'][0]['message']['tool_calls'][0]['function']['arguments']) == wrong
    feedback = json.loads(model.requests[1][0][-1]['content'])['error']
    sent = [event for event in trace['events'] if event['type'] == 'repair_feedback']
    assert len(sent) == 1 and sent[0]['step'] == 2 and sent[0]['source_step'] == 1
    assert sent[0]['feedback'] == model.requests[1][0][-1]['content']
    fields = json.loads(feedback.split(': ', 1)[1])['conflicts'][0]['proposal_query_fields']
    assert fields['selection'] == 'all' and fields['top_k'] is None
    assert 'order_by' not in fields and 'direction' not in fields and 'include_ties' not in fields
    assert 'direction controls row order only' in feedback
    assert trace['proposal']['actions'][0]['query']['selection'] == 'all'
    assert trace['plan']['steps'][0]['query']['top_k'] is None


def test_wrong_extreme_without_repair_is_never_silently_expanded():
    trace = draft_guided(SequenceModel([call('propose_analysis', proposal(selection='extreme'))]),
                         QUESTION, max_attempts=1, proposal_version='v2', check_request_rules=True)
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    assert trace['proposal']['actions'][0]['query']['selection'] == 'extreme'
    assert trace['plan']['steps'][0]['query']['top_k'] == 1 and 'preview' not in trace
    assert not any(event['type'] == 'repair_feedback' for event in trace['events'])


@pytest.mark.parametrize('question,expected', [
    ('列出平均长度最短的所有并列记录', dict(order_by='mean_length', direction='ascending', selection='extreme', top_k=None, include_ties=True)),
    ('列出记录，按正确题数升序取前1条并保留边界并列', dict(order_by='correct', direction='ascending', selection='top_k', top_k=1, include_ties=True)),
    ('列出记录，按准确率降序取前3条并保留边界并列', dict(order_by='accuracy', direction='descending', selection='top_k', top_k=3, include_ties=True)),
])
def test_feedback_maps_compiled_fields_back_to_the_right_proposal_selection(question, expected):
    plan = compile_proposal(question, ProposalV2.model_validate(proposal(pareto_only=False)))
    feedback = selection_conflict_feedback(check_requests(question, plan))
    fields = json.loads(feedback.split(': ', 1)[1])['conflicts'][0]['proposal_query_fields']
    assert all(fields[key] == value for key, value in expected.items())
    assert 'descending' not in fields and 'metric' not in fields and 'ranking' not in fields


def test_repeated_rule_conflict_has_two_requests_and_no_third_attempt():
    reply = proposal(selection='extreme')
    model = Capture([call('propose_analysis', reply), call('propose_analysis', reply, 'c2')])
    trace = draft_guided(model, QUESTION, proposal_version='v2', check_request_rules=True)
    assert trace['status'] == 'validation_stalled' and trace['model_calls'] == len(model.requests) == 2
    assert 'preview' not in trace and trace['request_checks']['has_conflicts']
    assert len([event for event in trace['events'] if event['type'] == 'repair_feedback']) == 1


def test_timeout_after_conflict_clears_stale_candidate_and_does_not_retry_provider():
    class Timeout(Capture):
        def complete(self, messages, tools):
            if len(messages) > 2:
                raise ModelError('timeout')
            return super().complete(messages, tools)

    trace = draft_guided(Timeout([call('propose_analysis', proposal(selection='extreme'))]),
                         QUESTION, proposal_version='v2', check_request_rules=True)
    assert trace['status'] == 'model_error' and trace['model_calls'] == 2 and trace['usage_incomplete']
    assert not {'plan', 'preview', 'proposal', 'request_checks'} & trace.keys()
    assert trace['events'][1]['report']['has_conflicts']


@pytest.mark.parametrize('message', [None, {'content': 'text only'},
                                     {'tool_calls': [{'function': {'name': 'propose_analysis', 'arguments': '{}'}}]}])
def test_v2_malformed_protocol_is_not_retried(message):
    trace = draft_guided(SequenceModel([message]), QUESTION, proposal_version='v2')
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    assert 'no protocol repair attempted' in trace['error']


@pytest.mark.parametrize('filename', ['a-pareto-r1.json', 'a-pareto-r2.json', 'b-pareto-r1.json', 'b-pareto-r2.json'])
def test_four_v08_first_replies_remain_v1_failures_and_are_not_silently_accepted_as_v2(filename):
    historical = json.loads((ROOT / 'docs/evaluations/v0.8/holdout' / filename).read_text())
    first_reply = next(event['message'] for event in historical['events'] if event['type'] == 'model')
    dataset = DatasetBundle.model_validate(historical['input_dataset'])
    old = draft_guided(SequenceModel([first_reply]), historical['question'], max_attempts=1,
                       dataset=dataset, check_request_rules=True)
    new = draft_guided(SequenceModel([first_reply]), historical['question'], max_attempts=1,
                       dataset=dataset, check_request_rules=True, proposal_version='v2')
    assert old['proposal_version'] == 'v1' and old['status'] == 'invalid_plan'
    assert old['plan']['steps'][0]['query']['top_k'] == 1
    assert old['request_checks']['has_conflicts']
    assert new['status'] == 'invalid_plan' and 'plan' not in new
    assert 'legacy metric/ranking are not accepted' in new['error']
    assert 'selection=all and top_k=null' in new['error']


def test_default_v1_keeps_historical_system_schema_and_feedback():
    legacy = {'actions': [dict(id='q1', clause_id='c1', operation='query', query=dict(
        metric='accuracy', ranking='maximum', top_k=None, include_ties=True, pareto_only=True))]}
    repaired = copy.deepcopy(legacy)
    repaired['actions'][0]['query']['ranking'] = 'descending'
    model = Capture([call('propose_analysis', legacy), call('propose_analysis', repaired, 'c2')])
    trace = draft_guided(model, QUESTION, check_request_rules=True)
    assert trace['proposal_version'] == 'v1' and trace['status'] == 'needs_review'
    assert model.requests[0][0][0]['content'] == GUIDED_SYSTEM
    assert model.requests[0][1][0]['function']['parameters'] == Proposal.model_json_schema()
    assert 'Limited request-rule conflict: ' in model.requests[1][0][-1]['content']
    assert 'expected=' in model.requests[1][0][-1]['content']
    assert not any(event['type'] == 'repair_feedback' for event in trace['events'])


def test_v2_sends_new_schema_and_system_without_changing_evidence():
    v2 = Capture([call('propose_analysis', proposal())])
    v1 = Capture([call('propose_analysis', {'actions': [dict(id='q1', clause_id='c1', operation='query',
              query=dict(metric='accuracy', ranking='descending', top_k=None, include_ties=True, pareto_only=True))]})])
    draft_guided(v1, QUESTION)
    draft_guided(v2, QUESTION, proposal_version='v2')
    assert v2.requests[0][0][0]['content'] == GUIDED_SYSTEM_V2
    assert v2.requests[0][1][0]['function']['parameters'] == ProposalV2.model_json_schema()
    assert v2.requests[0][0][1] == v1.requests[0][0][1]


@pytest.mark.parametrize('version', ['v3', None, 2])
def test_unknown_protocol_version_fails_before_model_request(version):
    with pytest.raises(ValueError, match='Proposal version'):
        draft_guided(SequenceModel([]), QUESTION, proposal_version=version)
