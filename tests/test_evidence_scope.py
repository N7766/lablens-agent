"""Deterministic scope regressions; no model calls or answer-quality claim."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lablens.evidence_scope import measurement_scope_findings, user_reference_contract

ROUND5 = Path(__file__).resolve().parents[1] / 'docs/evaluations/conversation-round5/development'


def assessment(explanation, *quotes, statement='Sable power failure recovery time', verdict='insufficient'):
    return {'assessments': [dict(statement=statement, verdict=verdict, explanation=explanation,
        quotes=[dict(chunk_id=f'c{i}', quote=quote) for i, quote in enumerate(quotes)])]}


def context(previous, *, answer='', focus=''):
    return SimpleNamespace(turns=[SimpleNamespace(question=previous, answer=answer)],
        state=SimpleNamespace(focus=focus))


def test_round5_harbor_final_explanation_is_not_supported_by_selected_quotes():
    final = json.loads((ROUND5 / 'run/harbor/harbor-t2/task-final.json').read_text())['result']
    findings = measurement_scope_findings(final)
    assert len(findings) == 1
    assert findings[0]['assessment_index'] == 2
    assert findings[0]['rule'] == 'historical_nonmeasurement_not_supported_by_selected_quotes'
    assert findings[0]['claim'] == 'R-17 未测量断电后的恢复时间'


@pytest.mark.parametrize('explanation', [
    'R-17 未测量 Sable 断电后的恢复时间。',
    'R-17 没有测量 Sable 断电后的恢复时间。',
    'R-17 从未测试 Sable 停电后的恢复时间。',
    'R-17: Sable power failure recovery time was not measured.',
    'R-17 did not measure Sable power failure recovery time.',
    'R-17 has never been tested for Sable power failure recovery time.',
    'In R-17, Sable power failure recovery time is unmeasured.',
])
def test_missing_record_does_not_establish_nonmeasurement(explanation):
    result = measurement_scope_findings(assessment(explanation,
        'The R-17 evidence package provides no maximum recovery time after a power failure for either candidate.',
        'No power-failure recovery measurement is included in the Sable result table.'))
    assert result and all(f['matching_explicit_nonmeasurement'] == 0 for f in result)


@pytest.mark.parametrize('quote', [
    'R-17 Sable power failure recovery time: unknown.',
    'R-17 Sable power failure recovery time: N/A.',
    'R-17 Sable power failure recovery time: null.',
    'R-17 Sable power failure recovery time: no record.',
    'R-17 Sable power failure recovery time is not available in this table.',
    'R-17: Sable recovery time after a power failure is missing.',
])
def test_unknown_values_never_license_historical_nonmeasurement(quote):
    assert measurement_scope_findings(assessment('R-17: Sable power failure recovery time was not measured.', quote))


@pytest.mark.parametrize('quote', [
    'R-09 Sable power failure recovery time was not measured.',
    'R-17 Quill power failure recovery time was not measured.',
    'R-17 Sable success rate was not measured.',
    'R-17 Sable median nightly recovery time was not measured.',
    'R-17 Sable recovery time was not measured.',
    'Unrelated temperature was not measured. R-17 Sable power failure recovery time is unknown.',
    'R-17 Sable power failure recovery time: unknown. R-17 Quill power failure recovery time was not measured.',
])
def test_nonmeasurement_phrase_requires_same_subject_run_and_metric(quote):
    assert measurement_scope_findings(assessment('R-17: Sable power failure recovery time was not measured.', quote))


@pytest.mark.parametrize(('explanation', 'quote'), [
    ('R-17: Sable power failure recovery time was not measured.',
     'R-17: Sable power failure recovery time was not measured.'),
    ('R-17 未测量 Sable 断电后的恢复时间。',
     'R-17: Sable recovery time after a power failure was not measured.'),
    ('R-17 未测量 Sable 断电后的最大恢复时间。',
     'R-17: Sable maximum recovery time after a power failure was not measured.'),
    ('Sable 的温度未测量。', 'Sable temperature was not measured.'),
])
def test_explicit_matching_nonmeasurement_source_is_allowed(explanation, quote):
    assert measurement_scope_findings(assessment(explanation, quote, statement=explanation)) == []


def test_nonmeasurement_claim_omitting_object_cannot_borrow_other_objects_source():
    # The claim's run matches, but the requested Sable object is essential too.
    assert measurement_scope_findings(assessment('R-17 未测量断电后的恢复时间。',
        'R-17 Quill power failure recovery time was not measured.'))


def test_conflicting_selected_measurement_is_not_silently_resolved_as_unmeasured():
    result = measurement_scope_findings(assessment('R-17: Sable power failure recovery time was not measured.',
        'R-17: Sable power failure recovery time was not measured.',
        'R-17: Sable power failure recovery time was measured at 180 seconds.'))
    assert result[0]['rule'] == 'selected_measurement_scope_conflict'
    assert result[0]['matching_explicit_nonmeasurement'] == 1
    assert result[0]['matching_positive_measurement'] == 1


def test_conflict_about_other_subject_does_not_invalidate_explicit_unmeasured_target():
    assert measurement_scope_findings(assessment('R-17: Sable power failure recovery time was not measured.',
        'R-17: Sable power failure recovery time was not measured.',
        'R-17: Quill power failure recovery time was measured at 180 seconds.')) == []


def test_source_attributed_conflict_can_be_described_without_asserting_world_history():
    assert measurement_scope_findings(assessment(
        'The report says R-17 Sable power failure recovery time was not measured; '
        'another record says it was measured. These selected sources conflict, so the historical outcome is unclear.',
        'R-17: Sable power failure recovery time was not measured.',
        'R-17: Sable power failure recovery time was measured at 180 seconds.')) == []


@pytest.mark.parametrize('explanation', [
    'R-17 资料包未提供 Sable 断电后的最大恢复时间，不能由夜间 median 推断。',
    '无法确认 R-17 是否未测量 Sable 停电恢复时间。',
    '记录里没有值不代表 R-17 未测量 Sable 的停电恢复时间。',
    'It is unknown whether R-17 Sable power failure recovery time was not measured.',
    'Missing data does not mean R-17 Sable power failure recovery time was not measured.',
    'The selected sources do not establish whether R-17 measured Sable power failure recovery time.',
])
def test_missingness_and_explicit_uncertainty_are_not_historical_denials(explanation):
    assert measurement_scope_findings(assessment(explanation)) == []


def test_later_uncertainty_does_not_excuse_prior_historical_denial():
    findings = measurement_scope_findings(assessment('R-17 未测量 Sable 停电恢复时间，但是否发生停电仍未知。'))
    assert len(findings) == 1


@pytest.mark.parametrize('verdict', ['insufficient', 'contradicted'])
def test_unresolved_or_refuted_proposition_is_not_itself_positive_assertion(verdict):
    assert measurement_scope_findings(assessment('The supplied records do not establish this proposition.',
        statement='R-17 Sable power failure recovery time was not measured.', verdict=verdict)) == []


def test_supported_statement_cannot_hide_historical_denial_behind_generic_explanation():
    assert measurement_scope_findings(assessment('See the selected evidence.',
        statement='R-17 Sable power failure recovery time was not measured.', verdict='supported'))


def test_round5_current_user_reintroduced_pair_requires_clarification():
    question = json.loads((ROUND5 / 'run/cedar/cedar-t3/request.json').read_text())['question']
    result = user_reference_contract(question, context('更正，只看 Dune。'))
    assert result['requires_clarification']
    assert result['choices'] == ['Dune', 'Reed']
    assert result['rule'] == 'current_user_parallel_targets_with_singular_reference'
    assert result['draft_coverage_verified'] is False


@pytest.mark.parametrize(('question', 'choices'), [
    ('比较 Node-east 和 Node-west。它是否达标？', ['Node-east', 'Node-west']),
    ('比较“节点甲”和“节点乙”。请按它定稿。', ['节点甲', '节点乙']),
    ('Dune and Reed are candidates. Did it pass?', ['Dune', 'Reed']),
    ('Compare Dune and Reed. Did it pass? Cite your evidence.', ['Dune', 'Reed']),
    ('我现在同时整理 Night Restore R-17 和 Archive Restore R-09 两次记录。请按那次写。', ['R-17', 'R-09']),
])
def test_current_turn_pair_works_without_any_conversation(question, choices):
    result = user_reference_contract(question)
    assert result['requires_clarification'] and result['choices'] == choices


@pytest.mark.parametrize('question', [
    'Dune 和 Reed 都还是候选。两者分别是否达标？',
    'Dune 和 Reed 都还是候选。请按它们各自的情况写结论。',
    'Compare Dune and Reed. Do both pass?',
    'Dune 和 Reed 都还是候选。请按 Dune 写最终验收结论。',
    'Dune 和 Reed 都还是候选。更正，最后只看 Reed。它是否达标？',
    'Compare Dune and Reed. Correction: use Reed only. Did it pass?',
    'Compare Dune and Reed. Did it pass? Actually, give both results.',
])
def test_current_plural_request_and_explicit_narrowing_remain_authoritative(question):
    assert not user_reference_contract(question)['requires_clarification']


@pytest.mark.parametrize('previous', [
    'Compare Node-east and Node-west.',
    '把 Node-east 和 Node-west 放在一起看。',
])
def test_existing_previous_user_pair_behavior_is_preserved(previous):
    result = user_reference_contract('Did that machine pass?', context(previous))
    assert result['requires_clarification'] and result['choices'] == ['Node-east', 'Node-west']


def test_recent_single_target_correction_clears_old_pair():
    ctx = {'turns': [{'question': 'Compare Node-east and Node-west.'},
                     {'question': 'Correction: use Node-west only.'}]}
    assert not user_reference_contract('Did that machine pass?', ctx)['requires_clarification']


def test_current_user_explicit_correction_with_both_names_clears_previous_pair():
    assert not user_reference_contract('更正，我问的是 Dune，不是 Reed。它通过了吗？',
        context('Compare Dune and Reed.'))['requires_clarification']


def test_assistant_and_memory_cannot_introduce_pair():
    ctx = context('What measurements are available?', answer='Compare Node-east and Node-west.',
                  focus='Compare Dune and Reed.')
    assert not user_reference_contract('Did it pass?', ctx)['requires_clarification']


@pytest.mark.parametrize('previous', [
    'Compare Latency and Throughput.',
    'Input and Output are present.',
    'Read and Write are available.',
    'Compare long-term and out-of-sample.',
    'Compare Current and Previous.',
    'Both Required and Optional are shown.',
])
def test_ordinary_english_words_are_not_inferred_as_named_candidates(previous):
    assert not user_reference_contract('Did it pass?', context(previous))['requires_clarification']


def test_extra_source_run_and_sentence_initial_the_do_not_create_false_mismatch():
    assert measurement_scope_findings(assessment('Sable power failure recovery time was not measured.',
        'The R-17 Sable power failure recovery time was not measured.')) == []


def test_comma_run_prefix_cannot_borrow_different_runs_nonmeasurement():
    assert measurement_scope_findings(assessment('In R-17, Sable power failure recovery time was not measured.',
        'In R-09, Sable power failure recovery time was not measured.'))


def test_comma_run_prefix_keeps_matching_explicit_nonmeasurement_valid():
    assert measurement_scope_findings(assessment('In R-17, Sable power failure recovery time was not measured.',
        'In R-17, Sable power failure recovery time was not measured.')) == []


def test_nonmeasurement_for_other_object_in_same_clause_is_not_support():
    assert measurement_scope_findings(assessment('R-17: Sable power failure recovery time was not measured.',
        'R-17: Sable power failure recovery time was measured and Quill power failure recovery time was not measured.'))


def test_lowercase_hardware_identifiers_are_bound_without_accepting_generic_compounds():
    assert user_reference_contract('Did it pass?', context('Compare gpu-a and gpu-b.'))['choices'] == ['gpu-a', 'gpu-b']
    assert not user_reference_contract('Did it pass?', context('Compare Long-term and Out-of-sample.'))['requires_clarification']
    assert measurement_scope_findings(assessment('gpu-a power was not measured.', 'gpu-b power was not measured.',
        statement='gpu-a power'))
    assert measurement_scope_findings(assessment('gpu-a power was not measured.', 'gpu-a power was not measured.',
        statement='gpu-a power')) == []


def test_unhyphenated_run_identifiers_are_not_interchangeable():
    assert measurement_scope_findings(assessment('R17 Sable power failure recovery time was not measured.',
        'R09 Sable power failure recovery time was not measured.'))


def test_shared_pair_primitive_retains_spans_and_filters_ordinary_words():
    from lablens.evidence_scope import named_user_pairs
    text = '比较 Dune 和 Reed，后面可继续缩小范围。'
    pairs = named_user_pairs(text)
    assert len(pairs) == 1 and text[pairs[0][0]:pairs[0][1]] == 'Dune 和 Reed'
    assert pairs[0][2] == ['Dune', 'Reed']
    assert named_user_pairs('Compare Latency and Throughput.') == []


@pytest.mark.parametrize('explanation', [
    '没有证据表明 R-17 未测量 Sable 停电恢复时间。',
    'The source does not say R-17 Sable power failure recovery time was not measured.',
])
def test_absence_of_evidence_for_negative_history_is_not_itself_negative_history(explanation):
    assert measurement_scope_findings(assessment(explanation)) == []
