"""Finite task rules reject known wrong plans and abstain outside their grammar."""
import copy
import json

import pytest

from lablens.planning import AnalysisPlan
from lablens.task_checks import check_tasks, task_conflict_feedback


RUNS = ['run:a', 'run:b', 'run:c']
PAIR = '以run:a为基线比较run:b的准确率差'
DESCRIPTION = '说明长度单位与数据来源'


def compare(text=PAIR, **changes):
    return dict(id='cmp', kind='compare', request_excerpt=text,
                baseline=dict(run_id='run:a'), candidate=dict(run_id='run:b')) | changes


def plan(*steps, unhandled=None):
    return AnalysisPlan.model_validate(dict(steps=list(steps), unhandled=unhandled or []))


def report(question, value):
    return check_tasks(question, value, run_ids=RUNS)


@pytest.mark.parametrize('metric', ['正确题数差', '准确率差', '平均长度减少比例'])
def test_explicit_pair_uses_one_compare_with_the_exact_direction(metric):
    question = f'以run:a为基线比较run:b的{metric}'
    value = plan(compare(question))
    before = copy.deepcopy(value.model_dump())
    checked = report(question, value)
    assert checked['status'] == 'checked' and not checked['has_conflicts']
    assert checked['semantic_validation'] == 'not_performed'
    assert checked['units'][0]['state'] == 'matched'
    assert checked['units'][0]['expected'] == dict(operation='compare', baseline='run:a', candidate='run:b')
    assert value.model_dump() == before


@pytest.mark.parametrize('question', [DESCRIPTION, '说明验证集和显著性边界', '说明缺失字段与证据边界'])
def test_known_descriptions_need_the_describe_operation(question):
    checked = report(question, plan(dict(id='info', kind='describe', request_excerpt=question)))
    assert checked['status'] == 'checked'
    assert checked['units'][0]['expected'] == {'operation': 'describe'}


def test_reversed_direction_conflicts_in_both_fields():
    checked = report(PAIR, plan(compare(baseline=dict(run_id='run:b'), candidate=dict(run_id='run:a'))))
    unit = checked['units'][0]
    assert unit['state'] == 'conflict'
    assert len(unit['differences']) == 2
    assert unit['actual'][0]['baseline']['run_id'] == 'run:b'
    fields = json.loads(task_conflict_feedback(checked).split(': ', 1)[1])['conflicts'][0]['proposal_fields']
    assert fields == dict(clause_id='c1', operation='compare', baseline='run:a', candidate='run:b')


@pytest.mark.parametrize('field,reference', [('baseline', dict(run_id='run:c')),
                                          ('candidate', dict(query_step='selected'))])
def test_other_record_or_query_reference_does_not_satisfy_a_named_pair(field, reference):
    checked = report(PAIR, plan(compare(**{field: reference})))
    assert checked['has_conflicts'] and checked['units'][0]['state'] == 'conflict'


@pytest.mark.parametrize('question,wrong', [
    (PAIR, dict(id='info', kind='describe', request_excerpt=PAIR)),
    (DESCRIPTION, compare(DESCRIPTION)),
    (DESCRIPTION, dict(id='q', kind='query', request_excerpt=DESCRIPTION, query={})),
])
def test_wrong_kind_does_not_count_as_fulfilled(question, wrong):
    checked = report(question, plan(wrong))
    assert checked['units'][0]['state'] == 'conflict'
    assert 'operation' in checked['units'][0]['differences'][0]


@pytest.mark.parametrize('question', [PAIR, DESCRIPTION])
def test_unsupported_is_missing_for_a_known_supported_task(question):
    checked = report(question, plan(unhandled=[dict(request_excerpt=question, reason='missing_data')]))
    assert checked['units'][0]['state'] == 'missing'
    assert checked['units'][0]['unhandled_indexes'] == [0]
    assert checked['has_conflicts']


def test_correct_step_plus_unhandled_is_a_conflict():
    checked = report(PAIR, plan(compare(), unhandled=[dict(request_excerpt=PAIR, reason='ambiguous_request')]))
    assert checked['units'][0]['state'] == 'conflict'


def test_two_exact_steps_cannot_be_silently_matched_to_one_request():
    checked = report(PAIR, plan(compare(), compare(id='cmp2')))
    assert checked['units'][0]['state'] == 'ambiguous'


def test_partial_wrong_kind_cannot_hide_beside_a_correct_full_step():
    checked = report(PAIR, plan(compare(), dict(id='extra', kind='describe', request_excerpt='准确率差')))
    assert checked['units'][0]['state'] == 'conflict'
    assert checked['unexpected_step_ids'] == ['extra']
    assert checked['units'][0]['step_ids'] == ['cmp', 'extra']


def test_partial_correct_kind_is_not_full_clause_confirmation():
    checked = report(PAIR, plan(compare('准确率差')))
    assert checked['units'][0]['state'] == 'missing'
    assert checked['unexpected_step_ids'] == ['cmp']


def test_step_spanning_two_clauses_is_not_discarded_by_mapping():
    question = PAIR + '；' + DESCRIPTION
    checked = report(question, plan(compare(), dict(id='info', kind='describe', request_excerpt=DESCRIPTION),
                                    dict(id='extra', kind='describe', request_excerpt='准确率差；说明长度单位')))
    assert [unit['state'] for unit in checked['units']] == ['conflict', 'conflict']
    assert checked['unexpected_step_ids'] == ['extra']


def test_distinct_explicit_cross_clause_pairs_map_without_guessing_by_record():
    second = '以run:b为基线比较run:c的正确题数差'
    question = PAIR + '；' + second + '；' + DESCRIPTION
    checked = report(question, plan(compare(), compare(second, id='cmp2', baseline=dict(run_id='run:b'), candidate=dict(run_id='run:c')),
                                    dict(id='info', kind='describe', request_excerpt=DESCRIPTION)))
    assert checked['status'] == 'checked'
    assert [unit['step_ids'] for unit in checked['units']] == [['cmp'], ['cmp2'], ['info']]
    assert [unit['clause_id'] for unit in checked['units']] == ['c1', 'c2', 'c3']


def test_duplicate_clause_texts_cannot_share_one_step_as_two_confirmations():
    checked = report(PAIR + '；' + PAIR, plan(compare()))
    assert [unit['state'] for unit in checked['units']] == ['ambiguous', 'ambiguous']
    assert checked['unexpected_step_ids'] == ['cmp']


@pytest.mark.parametrize('question', [
    '不要' + PAIR, PAIR + '但不要使用这两个记录', PAIR + '并选择最好模型',
    '如果可能，' + PAIR, '说明长度单位与数据来源并推断训练成本',
    '不要说明长度单位与数据来源', '以run:a为基线比较所选结果的准确率差',
    '以run:missing为基线比较run:b的准确率差',
    '以run:a为基线比较run:missing的准确率差',
    '以run:a为基线比较run:b的准确率差和正确题数差',
])
def test_unsupported_or_unknown_id_language_abstains_without_partial_pass(question):
    checked = report(question, plan(dict(id='info', kind='describe', request_excerpt=question)))
    assert checked['status'] == 'not_assessed' and checked['units'] == []
    assert len(checked['unassessed']) == 1 and not checked['has_conflicts']


@pytest.mark.parametrize('left,right', [('“', '”'), ('"', '"'), ('「', '」'), ('`', '`')])
def test_quoted_context_abstains_even_when_quote_spans_clauses(left, right):
    question = left + PAIR + '；' + DESCRIPTION + right
    checked = report(question, plan(dict(id='info', kind='describe', request_excerpt=DESCRIPTION)))
    assert checked['units'] == [] and len(checked['unassessed']) == 2


def test_unrecognized_query_is_outside_this_checker_scope():
    query = '列出完整Pareto集合'
    checked = report(query + '；' + DESCRIPTION, plan(dict(id='q', kind='query', request_excerpt=query, query={}),
                                                    dict(id='info', kind='describe', request_excerpt=DESCRIPTION)))
    assert checked['status'] == 'checked' and len(checked['unassessed']) == 1
    assert checked['unexpected_step_ids'] == []


def test_known_pair_with_identical_ids_still_has_declared_direction():
    question = '以run:a为基线比较run:a的准确率差'
    checked = report(question, plan(compare(question, candidate=dict(run_id='run:a'))))
    assert checked['status'] == 'checked'
