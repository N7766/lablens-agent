"""Limited request checks: historical errors, counterexamples and receipt compatibility."""
import json
from pathlib import Path

import pytest

from lablens.guided_planning import Proposal, compile_proposal
from lablens.plan_checks import clauses
from lablens.planning import AnalysisPlan
from lablens.request_checks import check_requests
from lablens.workbench import verify


ROOT = Path(__file__).resolve().parents[1]
DOUBLE = '分别列出正确题数至少440的Pareto集合（准确率更高且长度更短），以及正确题数至少443的所有记录'


def query_plan(question, queries, excerpts=None):
    excerpt = clauses(question)[0]['text']
    return AnalysisPlan.model_validate({'steps': [
        {'id': f'q{i + 1}', 'kind': 'query',
         'request_excerpt': excerpts[i] if excerpts else excerpt, 'query': query}
        for i, query in enumerate(queries)
    ]})


@pytest.mark.parametrize('name,conflict', [
    ('guided-two-queries-one-clause-r1.json', True),
    ('guided-two-queries-one-clause-r2.json', True),
    ('guided_once-two-queries-one-clause-r1.json', True),
    ('guided_once-two-queries-one-clause-r2.json', False),
])
def test_published_v06_same_clause_failures_and_success_are_replayed(name, conflict):
    trace = json.loads((ROOT / 'docs/evaluations/v0.6/holdout' / name).read_text())
    plan = AnalysisPlan.model_validate(trace['plan'])
    report = check_requests(trace['question'], plan)
    assert report['has_conflicts'] is conflict
    assert report['status'] == ('conflict' if conflict else 'checked')
    assert len(report['units']) == 2
    assert [unit['expected']['min_correct'] for unit in report['units']] == [440, 443]
    assert [unit['expected']['pareto_only'] for unit in report['units']] == [True, False]
    if not conflict:
        assert all(unit['state'] == 'matched' for unit in report['units'])
    else:
        assert any(unit['state'] != 'matched' for unit in report['units'])


@pytest.mark.parametrize('minimum', [0, 442, 443])
def test_truncation_is_rejected_even_when_current_output_is_singleton_or_empty(minimum):
    question = f'列出正确题数至少{minimum}的所有记录'
    plan = query_plan(question, [{'min_correct': minimum, 'top_k': 1}])
    report = check_requests(question, plan)
    assert report['has_conflicts']
    assert report['units'][0]['state'] == 'conflict'
    assert report['units'][0]['differences']


def test_extremum_with_null_top_k_is_checked_after_compilation():
    question = '列出正确题数至少443的所有记录'
    proposal = Proposal.model_validate({'actions': [{
        'id': 'q1', 'clause_id': 'c1', 'operation': 'query', 'query': {
            'metric': 'correct', 'ranking': 'minimum', 'top_k': None,
            'include_ties': False, 'min_correct': 443,
        },
    }]})
    plan = compile_proposal(question, proposal)
    assert plan.steps[0].query.top_k == 1
    assert check_requests(question, plan)['has_conflicts']


def test_double_request_matching_uses_filters_and_pareto_not_query_order():
    plan = query_plan(DOUBLE, [
        {'min_correct': 443, 'top_k': None},
        {'min_correct': 440, 'pareto_only': True, 'top_k': None},
    ])
    report = check_requests(DOUBLE, plan)
    assert report['status'] == 'checked'
    assert report['units'][0]['step_ids'] == ['q2']
    assert report['units'][1]['step_ids'] == ['q1']


@pytest.mark.parametrize('queries', [
    [{'min_correct': 440, 'pareto_only': True}],
    [{'min_correct': 443, 'pareto_only': True}, {'min_correct': 440}],
    [{'min_correct': 440, 'pareto_only': True}, {'min_correct': 440}],
])
def test_missing_or_misbound_second_request_cannot_be_hidden_by_first_query(queries):
    report = check_requests(DOUBLE, query_plan(DOUBLE, queries))
    assert report['has_conflicts']
    assert any(unit['state'] == 'missing' for unit in report['units'])


def test_duplicate_candidates_are_ambiguous_even_if_both_queries_are_correct():
    question = '列出正确题数至少440的所有记录'
    report = check_requests(question, query_plan(question, [
        {'min_correct': 440}, {'min_correct': 440},
    ]))
    assert report['has_conflicts']
    assert report['units'][0]['state'] == 'ambiguous'
    assert set(report['units'][0]['step_ids']) == {'q1', 'q2'}


def test_unrequested_query_is_not_hidden_by_a_complete_matching_query():
    question = '列出正确题数至少440的所有记录'
    report = check_requests(question, query_plan(question, [
        {'min_correct': 440}, {'min_correct': 443},
    ]))
    assert report['has_conflicts']
    assert report['unexpected_step_ids'] == ['q2']


def test_partial_excerpt_cannot_hide_an_extra_query_in_a_recognized_clause():
    question = '列出正确题数至少440的所有记录'
    plan = query_plan(question, [{'min_correct': 440}, {'top_k': 1}],
                      excerpts=[question, '所有记录'])
    report = check_requests(question, plan)
    assert report['has_conflicts']
    assert 'q2' in report['unexpected_step_ids']


def test_each_recognized_unit_can_use_its_exact_original_excerpt():
    question = '  ' + DOUBLE + '；说明显著性边界。'
    queries = [{'min_correct': 440, 'pareto_only': True}, {'min_correct': 443}]
    report = check_requests(question, query_plan(question, queries))
    excerpts = [unit['text'] for unit in report['units']]
    assert len(excerpts) == 2
    assert all(question[unit['start']:unit['end']] == unit['text'] for unit in report['units'])
    exact = check_requests(question, query_plan(question, queries, excerpts=excerpts))
    assert exact['status'] == 'checked'
    assert [unit['step_ids'] for unit in exact['units']] == [['q1'], ['q2']]
    assert any(item['clause_id'] == 'c2' for item in exact['unassessed'])


@pytest.mark.parametrize('label,metric,descending', [
    ('准确率最高', 'accuracy', True),
    ('准确率最低', 'accuracy', False),
    ('平均长度最长', 'mean_length', True),
    ('平均长度最短', 'mean_length', False),
    ('正确题数最多', 'correct', True),
    ('正确题数最少', 'correct', False),
])
def test_all_tied_extrema_require_top_one_and_ties_not_unbounded_rows(label, metric, descending):
    question = f'找出{label}的所有并列记录'
    query = {'order_by': metric, 'descending': descending, 'top_k': 1, 'include_ties': True}
    assert check_requests(question, query_plan(question, [query]))['status'] == 'checked'
    for change in ({'top_k': None}, {'include_ties': False}, {'descending': not descending}):
        report = check_requests(question, query_plan(question, [query | change]))
        assert report['has_conflicts']


@pytest.mark.parametrize('label,metric,word,descending', [
    ('平均长度', 'mean_length', '升序', False),
    ('准确率', 'accuracy', '降序', True),
    ('正确题数', 'correct', '升序', False),
])
def test_explicit_top_k_with_boundary_ties_is_supported(label, metric, word, descending):
    question = f'查询正确题数至少438的记录，按{label}{word}取前2条并保留边界并列'
    query = {'min_correct': 438, 'order_by': metric, 'descending': descending,
             'top_k': 2, 'include_ties': True}
    assert check_requests(question, query_plan(question, [query]))['status'] == 'checked'
    for change in ({'top_k': 1}, {'include_ties': False}, {'descending': not descending}):
        assert check_requests(question, query_plan(question, [query | change]))['has_conflicts']


def test_complete_filter_conjunction_is_checked_without_dropping_a_bound():
    question = '查询正确题数至少438且准确率至少94.5%且准确率不高于95.2%且平均长度严格小于31.6词的全部实验'
    query = {'min_correct': 438, 'min_accuracy': .945, 'max_accuracy': .952, 'max_length': 31.6}
    report = check_requests(question, query_plan(question, [query]))
    assert report['status'] == 'checked'
    for key in query:
        missing = dict(query)
        del missing[key]
        assert check_requests(question, query_plan(question, [missing]))['has_conflicts']


def test_order_and_ties_are_not_invented_for_unbounded_set_requests():
    question = '列出正确题数至少440的完整Pareto集合'
    for metric, descending, ties in [('accuracy', True, True), ('correct', False, False)]:
        query = {'min_correct': 440, 'pareto_only': True, 'top_k': None,
                 'order_by': metric, 'descending': descending, 'include_ties': ties}
        assert check_requests(question, query_plan(question, [query]))['status'] == 'checked'


@pytest.mark.parametrize('question', [
    '不要列出所有记录，只要第一条',
    '列出所有记录这句话不需要执行',
    '请解释“列出所有记录”是什么意思',
    '“列出所有记录”',
    '不是所有记录都需要展示',
    '列出所有记录，但排除run:sft',
    '列出所有记录中准确率最高的一条',
    '列出正确题数至少440且训练费用低于10元的所有记录',
    '列出Pareto集合中准确率最高的所有并列记录',
    '从所有记录中取前2条',
    '列出正确题数至少440的所有记录，并列出正确题数至少443的所有记录',
    '列出正确题数至少440的所有记录，以及不要列出正确题数至少443的所有记录',
])
def test_negations_quotes_and_unconsumed_conditions_abstain_instead_of_false_conflict(question):
    report = check_requests(question, query_plan(question, [{'top_k': 1}]))
    assert report['status'] == 'not_assessed'
    assert not report['has_conflicts']
    assert not report['units']
    assert report['unassessed']


@pytest.mark.parametrize('question', [
    '解释“示例；列出所有记录；”的含义',
    '解释“示例：\n列出所有记录\n”这段话的含义',
    '解释"示例;列出所有记录;"的含义',
])
def test_quoted_commands_stay_quoted_across_clause_delimiters(question):
    plan = AnalysisPlan.model_validate({'steps': [{
        'id': 'explain', 'kind': 'describe', 'request_excerpt': question,
    }]})
    report = check_requests(question, plan)
    assert report['status'] == 'not_assessed'
    assert not report['has_conflicts']
    assert not report['units']


@pytest.mark.parametrize('version,expected_count', [('v0.5', 6), ('v0.6', 19)])
def test_all_published_plan_receipts_still_recompute_including_semantic_failures(version, expected_count):
    receipts = []
    for path in sorted((ROOT / 'docs/evaluations' / version / 'holdout').glob('*.json')):
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            receipt = data.get('execution', {}).get('receipt')
            if receipt and receipt.get('format') == 'lablens-plan-receipt/v1':
                receipts.append((path.name, receipt))
    assert len(receipts) == expected_count
    for name, receipt in receipts:
        assert verify(receipt)['status'] == 'verified', name
