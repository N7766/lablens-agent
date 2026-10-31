"""Explicit cross-turn comparison structure; no real-model quality claims."""
import copy
import json
from pathlib import Path

import pytest

from lablens.answer_coverage import (CoverageContractError, comparison_requirements,
                                     prepare_review_draft)
from lablens.assessment import resolve_selections
from lablens.answer_quality import check_review_scope, make_review_contract
from lablens.rag import _generation_payload
from test_answer_quality import assessment


QUESTION = '更正上一轮，当前目标已改变。请指出超时上限、并发数和失败处理这三项中哪些需要变更、哪些保留，并说明依据。'


def rows(*statements):
    return {'assessments': [assessment(statement=text)['assessments'][0] for text in statements]}


def subjects(q=QUESTION):
    return [row['subject'] for row in comparison_requirements(q)]


def test_real_sealed_draft_reserves_three_comparisons_without_rewriting_history():
    path = Path('docs/evaluations/conversation-round6/runs/round6-once/ar-t2-final-task.json')
    raw_bytes = path.read_bytes()
    raw = json.loads(raw_bytes)
    trace = raw['result']['agent']['trace']
    submit = next(event for event in trace if event['event'] == 'tool_started'
                  and event.get('name') == 'verify_and_submit_report')
    hits = raw['result']['retrieval']['hits']
    draft = resolve_selections(submit['arguments'], hits)
    before = copy.deepcopy(draft)
    prepared, meta = prepare_review_draft(raw['request']['question'], draft)
    assert subjects(raw['request']['question']) == ['总尝试次数', '重试次数', '4xx 处理']
    assert len(draft['assessments']) == 4 and len(prepared['assessments']) == 7
    assert len(meta['requested_comparisons']) == len(meta['added_review_slots']) == 3
    assert prepared['assessments'][:4] == before['assessments'] and draft == before
    assert path.read_bytes() == raw_bytes
    assert meta['semantic_coverage_verified'] is False
    for row in prepared['assessments'][4:]:
        assert row['verdict'] == 'insufficient' and row['quotes'] == row['citation_ids'] == []
        assert '旧回答不是证据' in row['explanation']
        assert '不是资料缺失' in row['explanation']
        assert '不足或矛盾' in row['explanation']


@pytest.mark.parametrize(('question', 'expected'), [
    (QUESTION, ['超时上限', '并发数', '失败处理']),
    ('与上一轮相比，请说明内存、线程和窗口中哪些改变、哪些保留。', ['内存', '线程', '窗口']),
    ('针对此前结论，请列出 timeout 和 retries 这两项中哪些需要修改、哪些沿用。', ['timeout', 'retries']),
    ('Compared with the previous answer, for latency, retries, and timeout, state which changed and which remain unchanged.',
     ['latency', 'retries', 'timeout']),
    ('For memory and threads, explain which changed and which stay the same relative to the prior conclusion.',
     ['memory', 'threads']),
])
def test_explicit_narrow_grammar_preserves_item_text(question, expected):
    assert subjects(question) == expected


@pytest.mark.parametrize('question', [
    '更正上一轮，现在只给新的超时上限、并发数和失败处理。',
    '请指出超时上限、并发数和失败处理中哪些变更、哪些保留。',  # no prior-answer anchor
    '与上一轮比较，哪些变更、哪些保留？',  # no explicit list
    '上一轮有错误，请指出超时上限中哪些变更、哪些保留。',
    '上一轮有错误，请指出超时上限、超时上限中哪些变更、哪些保留。',
    '请指出 A、B、C、D、E、F、G 中哪些变更、哪些保留，比较上一轮。',
    '与上一轮相比，请指出 A、B 中哪些变更。',
    '与上一轮相比，请指出 A、B 中哪些保留。',
    '与上一轮相比，请指出 A、B 中哪些变更、哪些保留；但不要比较，直接给当前值。',
    'Compared with the previous answer, for timeout and retries, state which changed and which remain; do not compare.',
    '请指出 A、B 中哪些变更、哪些保留，也请指出 C、D 中哪些变更、哪些保留，比较上一轮。',
])
def test_unrequested_ambiguous_negated_or_unbounded_comparisons_not_inferred(question):
    draft = rows('当前值')
    prepared, meta = prepare_review_draft(question, draft)
    assert prepared == draft and meta['requested_comparisons'] == []


def test_existing_complete_comparison_not_duplicated_in_eight_row_report():
    draft = rows('超时上限由 10 秒改为 20 秒。', '并发数保持不变。', '失败处理沿用上一轮。',
                 '其他一', '其他二', '其他三', '其他四', '其他五')
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert prepared == draft and meta['added_review_slots'] == []
    assert len(prepared['assessments']) == 8
    assert meta['semantic_coverage_verified'] is False


def test_incorrect_comparative_assertion_remains_for_review_not_certified_by_lexical_presence():
    draft = rows('超时上限由 9999 秒改为 1234 秒。', '并发数保持不变。', '失败处理沿用上一轮。')
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert prepared == draft and meta['added_review_slots'] == []
    assert not meta['semantic_coverage_verified']


def test_new_values_only_or_neighbour_change_marker_do_not_cover_other_comparisons():
    draft = rows('超时上限是 20 秒。', '并发数为 50，失败处理由重试改为失败。')
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert meta['added_review_slots'] == [r['statement'] for r in comparison_requirements(QUESTION)][:2]
    assert len(prepared['assessments']) == 4


def test_metric_heading_may_own_comparative_explanation():
    draft = rows('超时上限', '并发数', '失败处理')
    for row, explanation in zip(draft['assessments'], ['从 10 秒改为 20 秒。', '保持不变。', '仍然不重试。']):
        row['explanation'] = explanation
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert prepared == draft and meta['added_review_slots'] == []


def test_heading_does_not_borrow_comparison_from_different_metric_explanation():
    draft = rows('超时上限为 20 秒。')
    draft['assessments'][0]['explanation'] = '并发数由 25 改为 50。'
    _, meta = prepare_review_draft(QUESTION, draft)
    assert meta['added_review_slots'] == [r['statement'] for r in comparison_requirements(QUESTION)][::2]


def test_uncertainty_and_conflicting_sources_keep_verdict_and_quotes_for_review():
    draft = rows('超时上限：无法判断是否变更。', '并发数保持不变。', '失败处理是否沿用上一轮？')
    draft['assessments'][0].update(verdict='insufficient', explanation='旧配置缺失，无法判断是否变更。', citation_ids=[], quotes=[])
    draft['assessments'][1].update(verdict='contradicted', explanation='资料甲写了 10，资料乙写了 20，互相矛盾。')
    before = copy.deepcopy(draft)
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert prepared['assessments'][:3] == before['assessments'] and draft == before
    assert meta['added_review_slots'] == [comparison_requirements(QUESTION)[2]['statement']]
    assert '不足或矛盾' in prepared['assessments'][-1]['explanation']


def test_server_slots_are_idempotent_without_claiming_answer_completeness():
    draft, first = prepare_review_draft(QUESTION, rows('当前值'))
    repeated, second = prepare_review_draft(QUESTION, draft)
    assert len(first['added_review_slots']) == 3
    assert second['added_review_slots'] == [] and repeated == draft
    assert second['semantic_coverage_verified'] is False


def test_full_capacity_fails_without_deleting_rows_or_fabricating_comparisons():
    draft = rows(*(f'Metric {i}' for i in range(8)))
    before = copy.deepcopy(draft)
    with pytest.raises(CoverageContractError, match='Required review slots exceed') as caught:
        prepare_review_draft(QUESTION, draft)
    assert draft == before and caught.value.assessment_count == 8
    assert caught.value.missing_review_slots == [r['statement'] for r in comparison_requirements(QUESTION)]
    assert caught.value.missing_overall_slots == caught.value.missing_review_slots  # old consumer compatibility


def test_exact_capacity_and_fixed_review_contract_preserve_new_comparison_questions():
    prepared, coverage = prepare_review_draft(QUESTION, rows(*(f'Metric {i}' for i in range(5))))
    assert len(prepared['assessments']) == 8
    contract = make_review_contract(QUESTION, prepared)
    contract['explicit_coverage_slots'] = coverage
    payload = _generation_payload(QUESTION, [], {'model': 'controlled'}, draft=prepared,
                                  context_data={'scoped_review_contract': contract})
    schema = payload['response_format']['json_schema']['schema']['properties']['assessments']
    assert schema['minItems'] == schema['maxItems'] == 8
    invalid = copy.deepcopy(prepared)
    invalid['assessments'].pop()
    with pytest.raises(ValueError, match='original proposition'):
        check_review_scope(QUESTION, prepared, invalid)


def test_same_clause_neighbour_predicate_does_not_cover_the_first_metric():
    draft = rows('超时上限为 20 秒且并发数由 25 改为 50。', '失败处理保持不变。')
    _, meta = prepare_review_draft(QUESTION, draft)
    assert meta['added_review_slots'] == [comparison_requirements(QUESTION)[0]['statement']]


def test_bare_comparison_question_is_not_a_completed_comparison_row():
    draft = rows('超时上限需要变更吗？', '并发数保持不变。', '失败处理沿用上一轮。')
    _, meta = prepare_review_draft(QUESTION, draft)
    assert meta['added_review_slots'] == [comparison_requirements(QUESTION)[0]['statement']]


def test_multi_metric_statement_cannot_borrow_unlabelled_comparison_explanation():
    question = '与上一轮相比，请指出超时上限、并发数中哪些需要变更、哪些保留。'
    draft = rows('超时上限为20秒，并发数由25改为50。')
    draft['assessments'][0]['explanation'] = '从25改为50。'
    _, meta = prepare_review_draft(question, draft)
    assert meta['added_review_slots'] == [comparison_requirements(question)[0]['statement']]
