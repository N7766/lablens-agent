"""Task checks use the same prompt/schema and the existing two-request budget."""
import copy
import json

import pytest

from lablens.guided_planning import draft_guided
from lablens.datasets import DatasetBundle
from lablens.model import ModelError
from lablens.planning import PlanRequest, execute_plan, verify_plan
from lablens.tools import ToolBox
from test_agent import SequenceModel, call


PAIR = '以run:sft为基线比较run:dpo-best的准确率差'
DESCRIPTION = '说明长度单位与数据来源'


def compare(**changes):
    return dict(id='cmp', clause_id='c1', operation='compare', baseline='run:sft', candidate='run:dpo-best') | changes


def reply(*actions, call_id='c1'):
    return call('propose_analysis', {'actions': list(actions)}, call_id)


def run(model, question=PAIR, **kwargs):
    return draft_guided(model, question, proposal_version='v2', check_request_rules=True, **kwargs)


class Capture(SequenceModel):
    def __init__(self, sequence):
        super().__init__(sequence)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return super().complete(messages, tools)


def test_task_checks_off_and_on_have_identical_initial_prompt_schema_and_evidence():
    wrong = reply(compare(baseline='run:dpo-best', candidate='run:sft'))
    old, new = Capture([wrong]), Capture([wrong])
    baseline = run(old, max_attempts=1)
    treatment = run(new, max_attempts=1, check_task_rules=True)
    assert old.requests[0] == new.requests[0]
    assert baseline['status'] == 'needs_review' and baseline['task_rules_enabled'] is False
    assert 'task_checks' not in baseline and not any(event['type'] == 'task_check' for event in baseline['events'])
    assert treatment['status'] == 'invalid_plan' and treatment['task_rules_enabled'] is True
    assert treatment['plan'] == baseline['plan'] and 'preview' not in treatment
    assert treatment['task_checks']['has_conflicts']


def test_reverse_direction_is_visible_and_repaired_only_by_a_replacement_reply():
    wrong = reply(compare(baseline='run:dpo-best', candidate='run:sft'))
    model = Capture([wrong, reply(compare(), call_id='c2')])
    trace = run(model, check_task_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    assert next(event for event in trace['events'] if event['type'] == 'model')['message'] == wrong
    reports = [event['report'] for event in trace['events'] if event['type'] == 'task_check']
    assert [report['has_conflicts'] for report in reports] == [True, False]
    feedback = json.loads(model.requests[1][0][-1]['content'])['error']
    fields = json.loads(feedback.split(': ', 1)[1])['conflicts'][0]['proposal_fields']
    assert fields == dict(clause_id='c1', operation='compare', baseline='run:sft', candidate='run:dpo-best')
    assert [event for event in trace['events'] if event['type'] == 'repair_feedback'][0]['feedback'] == model.requests[1][0][-1]['content']
    output = execute_plan(PlanRequest(question=PAIR, plan=trace['plan'], dataset=trace['input_dataset'], preview_id=trace['preview']['preview_id']))
    assert output['results'][0]['result']['additional_correct'] == 3
    assert verify_plan(output['receipt'])['status'] == 'verified'


def test_supported_describe_cannot_be_replaced_by_unsupported():
    model = Capture([reply(dict(id='info', clause_id='c1', operation='unsupported', reason='missing_data')),
                     reply(dict(id='info', clause_id='c1', operation='describe'), call_id='c2')])
    trace = run(model, DESCRIPTION, check_task_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    assert trace['plan']['steps'][0]['kind'] == 'describe' and not trace['plan']['unhandled']
    first = next(event['report'] for event in trace['events'] if event['type'] == 'task_check')
    assert first['units'][0]['state'] == 'missing'


def test_mixed_query_pair_and_description_errors_share_the_same_repair_budget():
    query = '列出完整Pareto集合'
    question = query + '；' + PAIR + '；' + DESCRIPTION
    bad_query = dict(id='q', clause_id='c1', operation='query', query=dict(order_by='accuracy', direction='descending',
        selection='extreme', top_k=None, include_ties=True, pareto_only=True))
    good_query = copy.deepcopy(bad_query)
    good_query['query']['selection'] = 'all'
    model = Capture([reply(bad_query, compare(clause_id='c2', baseline='run:dpo-best', candidate='run:sft'),
                           dict(id='info', clause_id='c3', operation='unsupported', reason='missing_data')),
                     reply(good_query, compare(clause_id='c2'), dict(id='info', clause_id='c3', operation='describe'), call_id='c2')])
    trace = run(model, question, check_task_rules=True)
    assert trace['status'] == 'needs_review' and trace['model_calls'] == 2
    error = json.loads(model.requests[1][0][-1]['content'])['error']
    query_feedback, task_feedback = error.split('\n', 1)
    query_fields = json.loads(query_feedback.split(': ', 1)[1])['conflicts'][0]['proposal_query_fields']
    task_fields = [row['proposal_fields'] for row in json.loads(task_feedback.split(': ', 1)[1])['conflicts']]
    assert query_fields['selection'] == 'all'
    assert task_fields == [dict(clause_id='c2', operation='compare', baseline='run:sft', candidate='run:dpo-best'),
                           dict(clause_id='c3', operation='describe')]
    assert len(error) < 8192
    assert trace['request_checks']['status'] == 'checked' and trace['task_checks']['status'] == 'checked'


def test_long_named_ids_keep_complete_actionable_json_in_combined_feedback():
    baseline, candidate = 'run:' + 'a' * 400, 'run:' + 'b' * 400
    data = copy.deepcopy(ToolBox().runs[:2])
    data[0]['id'], data[1]['id'] = baseline, candidate
    dataset = DatasetBundle.model_validate(dict(runs=data, notes=[]))
    question = '列出完整Pareto集合；' + f'以{baseline}为基线比较{candidate}的准确率差'
    bad_query = dict(id='q', clause_id='c1', operation='query', query=dict(order_by='accuracy', direction='descending',
        selection='extreme', top_k=None, include_ties=True, pareto_only=True))
    good_query = copy.deepcopy(bad_query)
    good_query['query']['selection'] = 'all'
    model = Capture([reply(bad_query, compare(clause_id='c2', baseline=candidate, candidate=baseline)),
                     reply(good_query, compare(clause_id='c2', baseline=baseline, candidate=candidate), call_id='c2')])
    trace = run(model, question, check_task_rules=True, dataset=dataset)
    assert trace['status'] == 'needs_review'
    error = json.loads(model.requests[1][0][-1]['content'])['error']
    assert 1800 < len(error) < 8192
    query_feedback, task_feedback = error.split('\n', 1)
    assert json.loads(query_feedback.split(': ', 1)[1])['conflicts'][0]['proposal_query_fields']['selection'] == 'all'
    fields = json.loads(task_feedback.split(': ', 1)[1])['conflicts'][0]['proposal_fields']
    assert fields['baseline'] == baseline and fields['candidate'] == candidate


def test_repeated_task_conflict_stops_at_two_requests_without_changing_the_plan():
    wrong = reply(compare(baseline='run:dpo-best', candidate='run:sft'))
    model = Capture([wrong, wrong])
    trace = run(model, check_task_rules=True)
    assert trace['status'] == 'validation_stalled' and trace['model_calls'] == len(model.requests) == 2
    assert trace['plan']['steps'][0]['baseline']['run_id'] == 'run:dpo-best'
    assert 'preview' not in trace and len([event for event in trace['events'] if event['type'] == 'repair_feedback']) == 1


def test_single_attempt_exposes_failure_without_emitting_an_unsent_feedback_event():
    trace = run(SequenceModel([reply(compare(baseline='run:dpo-best', candidate='run:sft'))]),
                max_attempts=1, check_task_rules=True)
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    assert not any(event['type'] == 'repair_feedback' for event in trace['events'])


def test_provider_failure_after_conflict_clears_stale_task_reports():
    class Timeout(Capture):
        def complete(self, messages, tools):
            if len(messages) > 2:
                raise ModelError('timeout')
            return super().complete(messages, tools)

    trace = run(Timeout([reply(compare(baseline='run:dpo-best', candidate='run:sft'))]), check_task_rules=True)
    assert trace['status'] == 'model_error' and trace['model_calls'] == 2 and trace['usage_incomplete']
    assert not {'plan', 'preview', 'proposal', 'task_checks', 'request_checks'} & trace.keys()
    assert any(event['type'] == 'task_check' and event['report']['has_conflicts'] for event in trace['events'])


@pytest.mark.parametrize('message', [None, {'content': 'text only'}])
def test_malformed_protocol_does_not_trigger_task_repair(message):
    trace = run(SequenceModel([message]), check_task_rules=True)
    assert trace['status'] == 'invalid_plan' and trace['model_calls'] == 1
    assert not any(event['type'] in {'task_check', 'repair_feedback'} for event in trace['events'])
