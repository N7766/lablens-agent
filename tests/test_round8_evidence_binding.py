"""Controlled row-binding regressions; not model/semantic quality evaluation."""
import copy
import json
from pathlib import Path

import pytest

from lablens.answer_quality import enforce_review_checks, make_review_contract, selected_evidence_findings
from lablens.evidence_binding import inspect_evidence_binding

FROZEN = Path(__file__).resolve().parents[1] / 'docs/evaluations/conversation-round7/runs/round7-once'
QUESTION = 'Compare Maple and Birch candidates and give each overall acceptance verdict.'


def report(statement='Birch overall acceptance.', explanation='Maple failed acceptance.',
           quotes=('Maple: median restore latency 150 seconds.',), **changes):
    row = dict(statement=statement, explanation=explanation, verdict='supported',
               basis='documented_fact', citation_ids=['c'],
               quotes=[dict(chunk_id='c', quote=text) for text in quotes])
    row.update(changes)
    return {'assessments': [row]}


def config(explanation='max_retries=2.', quotes=('max_retries=2',), **changes):
    return report(statement='Current configured retry ceiling.', explanation=explanation,
                  quotes=quotes, basis='current_configuration', **changes)


def frozen(name):
    result = json.loads((FROZEN / f'{name}-final-task.json').read_text())['result']
    return result['question'], dict(assessments=result['assessments'])


def test_actual_harbor_failure_is_bound_to_wrong_subject_without_rescuing_quotes():
    question, raw = frozen('harbor-t1')
    before = copy.deepcopy(raw)
    assert selected_evidence_findings(raw) == []  # Old numeric/scope checks missed this failure.
    protected, quality = enforce_review_checks(question, raw, raw)
    assert {finding['assessment_index'] for finding in quality['findings']} == {5}
    assert [(finding['rule'], finding['field']) for finding in quality['findings']] == [
        ('explicit_subject_replaced_by_peer', 'explanation'),
        ('explicit_subject_replaced_by_peer', 'selected_quotes')]
    assert protected['assessments'][:5] == raw['assessments'][:5]
    withheld = protected['assessments'][5]
    assert withheld['verdict'] == 'insufficient' and '原结论未发布' in withheld['explanation']
    for key in ('statement', 'quotes', 'citation_ids'):
        assert withheld[key] == raw['assessments'][5][key]
    assert quality['withheld_originals'] == [dict(assessment_index=5,
        original_verdict='supported', original_explanation=raw['assessments'][5]['explanation'])]
    assert raw == before


def test_actual_kiteload_alert_cannot_support_config_scalar_and_unknown_rows_survive():
    question, raw = frozen('kl-t2')
    assert selected_evidence_findings(raw) == []
    protected, quality = enforce_review_checks(question, raw, raw)
    assert [finding['assessment_index'] for finding in quality['findings']] == [0, 1]
    assert {finding['rule'] for finding in quality['findings']} == {'configuration_literal_not_in_selected_quotes'}
    assert [row['verdict'] for row in protected['assessments']] == ['insufficient'] * 4
    assert protected['assessments'][2:] == raw['assessments'][2:]
    assert [row['assessment_index'] for row in quality['withheld_originals']] == [0, 1]


@pytest.mark.parametrize('name', ['harbor-t2', 'kl-t1', 'ar-t1', 'ar-t2'])
def test_existing_reports_are_not_changed_by_this_binding_contract(name):
    # ar-t2 still contains the known wrong comparison refusal. No finding here
    # means out of these two contracts' scope, not a semantic pass.
    question, raw = frozen(name)
    binding = inspect_evidence_binding(question, raw)
    assert binding['findings'] == []
    assert binding['semantic_entailment_verified'] is False


@pytest.mark.parametrize(('explanation', 'quotes', 'fields'), [
    ('Maple failed acceptance.', ('Birch: observed latency 149 seconds.',), []),
    ('Birch failed acceptance.', ('Maple: observed latency 150 seconds.',), []),
    ('Maple failed acceptance.', ('Maple: observed latency 150 seconds.',), ['explanation', 'selected_quotes']),
])
def test_controlled_peer_replacement_is_detected_per_field(explanation, quotes, fields):
    binding = inspect_evidence_binding(QUESTION, report(explanation=explanation, quotes=quotes))
    assert [finding['field'] for finding in binding['findings']] == fields
    assert all(finding['expected_subject'] == 'Birch' for finding in binding['findings'])


def test_valid_peer_comparison_with_pronoun_is_not_mistaken_for_subject_replacement():
    raw = report(statement='Birch 的验收结论。', explanation='与 Maple 不同，它通过验收。',
                 quotes=('Birch passed acceptance. Maple failed acceptance.',))
    binding = inspect_evidence_binding(QUESTION, raw)
    assert binding['findings'] == []
    assert binding['assessments'][0]['subject']['fields']['explanation']['reason'] == 'peer_only_one_field_no_subject_resolution'
    assert binding['subject_predicate_binding_verified'] is False


def test_valid_subject_and_shared_criteria_are_preserved():
    raw = report(explanation='Birch has 149 seconds, which is below the 150 seconds limit.',
                 quotes=('Birch: observed latency 149 seconds.', 'The limit is 150 seconds.'))
    binding = inspect_evidence_binding(QUESTION, raw)
    assert binding['findings'] == []
    assert binding['assessments'][0]['subject']['fields']['selected_quotes']['state'] == 'checked_literal_presence'
    assert binding['subject_predicate_binding_verified'] is False


@pytest.mark.parametrize(('statement', 'explanation', 'quotes', 'reason'), [
    ('Maple and Birch results.', 'Maple and Birch values are swapped.',
     ('Maple: 150 seconds. Birch: 149 seconds.',), 'statement_has_zero_or_multiple_pair_names'),
    ('The overall result.', 'Maple failed.', ('Maple failed.',), 'statement_has_zero_or_multiple_pair_names'),
])
def test_multi_subject_or_unnamed_statement_is_explicitly_unverified(statement, explanation, quotes, reason):
    binding = inspect_evidence_binding(QUESTION, report(statement, explanation, quotes))
    assert binding['findings'] == []
    assert binding['assessments'][0]['subject'] == dict(state='unverified', reason=reason)


def test_comention_of_both_subjects_cannot_be_reported_as_predicate_verification():
    raw = report(explanation='Birch has Maple value 150 seconds.',
                 quotes=('Maple: 150 seconds. Birch: 149 seconds.',))
    binding = inspect_evidence_binding(QUESTION, raw)
    assert binding['findings'] == []  # Known semantic error outside this narrow guard.
    fields = binding['assessments'][0]['subject']['fields']
    assert all(row['state'] == 'unverified' for row in fields.values())
    assert all(row['reason'] == 'both_names_present_no_predicate_binding' for row in fields.values())
    assert binding['subject_predicate_binding_verified'] is False


@pytest.mark.parametrize('question', ['Result?', 'Compare Maple and Birch; also compare Oak and Pine.'])
def test_no_unique_current_pair_does_not_infer_names_from_model_output(question):
    binding = inspect_evidence_binding(question, report())
    assert binding['findings'] == []
    assert binding['assessments'][0]['subject']['reason'] == 'no_single_explicit_current_user_pair'


@pytest.mark.parametrize('explanation', ['It failed acceptance.', 'Birchwood failed acceptance.'])
def test_pronouns_and_prefix_collisions_are_not_resolved_as_subjects(explanation):
    binding = inspect_evidence_binding(QUESTION, report(explanation=explanation, quotes=('The acceptance rule.',)))
    fields = binding['assessments'][0]['subject']['fields']
    assert binding['findings'] == []
    assert all(row['state'] == 'unverified' for row in fields.values())


@pytest.mark.parametrize('verdict', ['insufficient', 'contradicted'])
def test_false_or_unknown_config_propositions_do_not_assert_their_literal(verdict):
    binding = inspect_evidence_binding('Configuration?', config(verdict=verdict, quotes=('No configuration snapshot.',)))
    assert binding['findings'] == []
    assert binding['assessments'][0]['configuration']['reason'] == 'not_supported_current_configuration'


def test_insufficient_subject_is_not_forced_to_select_missing_observation():
    raw = report(explanation='Only Maple was observed; Birch remains unknown.',
                 quotes=('Maple: measured latency 150 seconds.',), verdict='insufficient')
    binding = inspect_evidence_binding(QUESTION, raw)
    assert binding['findings'] == []
    assert binding['assessments'][0]['subject']['reason'] == 'insufficient_proposition'


@pytest.mark.parametrize('quote', ['badge_id=2', 'max_retries=3', 'max_retries_extra=2', 'MAX_RETRIES=2'])
def test_matching_number_wrong_key_value_or_case_is_not_the_config_assignment(quote):
    binding = inspect_evidence_binding('Configuration?', config(quotes=(quote,)))
    assert [row['rule'] for row in binding['findings']] == ['configuration_literal_not_in_selected_quotes']


@pytest.mark.parametrize('quote', ['max_retries: 2', 'max_retries = 2.0', '"max_retries": 2',
                                   'Current capture: max_retries=2; badge_id=7.'])
def test_exact_config_scalar_is_anchored_independent_of_source_filename(quote):
    binding = inspect_evidence_binding('Configuration?', config(quotes=(quote,)))
    assert binding['findings'] == []
    assert binding['assessments'][0]['configuration']['checks'][0]['state'] == 'checked_literal_presence'
    assert binding['source_authority_verified'] is False


@pytest.mark.parametrize('prose', ['If max_retries=2, total attempts would be 3.',
    '假设 max_retries=2，则总尝试为3。', 'max_retries=2?', 'max_retries=2？',
    'Whether max_retries=2 remains unknown.', 'max_retries=2 is false.',
    'It is not max_retries=2.', '并非 max_retries=2。', '例如 max_retries=2。'])
def test_nonassertive_literal_sentences_are_recorded_as_skipped(prose):
    binding = inspect_evidence_binding('Configuration?', config(explanation=prose, quotes=('A receipt.',)))
    assert binding['findings'] == []
    check = binding['assessments'][0]['configuration']
    assert check['state'] == 'unverified' and check['skipped_literals']


def test_one_unknown_sentence_does_not_hide_a_different_affirmative_assignment():
    binding = inspect_evidence_binding('Configuration?', config(
        explanation='Whether max_retries=2 remains unknown. 当前设置 batch_size=4。', quotes=('A receipt.',)))
    assert [row['key'] for row in binding['findings']] == ['batch_size']


@pytest.mark.parametrize('value', ['true', 'false', 'null', '-2', '2e1'])
def test_bool_null_signed_and_bounded_exponent_literals(value):
    binding = inspect_evidence_binding('Configuration?', config(
        explanation=f'test_value={value}', quotes=(f'test_value: {value}',)))
    assert binding['findings'] == []
    assert binding['assessments'][0]['configuration']['checks'][0]['state'] == 'checked_literal_presence'


@pytest.mark.parametrize('prose', ['maxRetries=2', 'max_retries="two"', 'max_retries=1e999999',
                                   'max_retries=2x', 'max_retries>=2', '最多重试2次。'])
def test_outside_literal_grammar_is_visible_unverified_not_a_pass(prose):
    binding = inspect_evidence_binding('Configuration?', config(explanation=prose, quotes=('A receipt.',)))
    assert binding['findings'] == []
    assert binding['assessments'][0]['configuration']['state'] == 'unverified'


def test_draft_contract_exposes_row_local_gap_to_existing_one_review():
    raw = config(quotes=('badge_id=2',))
    contract = make_review_contract('Configuration?', raw)
    assert contract['evidence_binding']['findings'][0]['rule'] == 'configuration_literal_not_in_selected_quotes'
    reviewed = copy.deepcopy(raw)
    reviewed['assessments'][0]['quotes'] = [dict(chunk_id='c', quote='max_retries=2')]
    protected, quality = enforce_review_checks('Configuration?', raw, reviewed)
    assert protected == reviewed and quality['findings'] == [] and quality['withheld_originals'] == []


def test_missing_config_source_cannot_be_rescued_by_another_assessment():
    raw = config(quotes=('badge_id=2',))
    raw['assessments'] += config()['assessments']
    raw['assessments'][1]['statement'] = 'Another current setting row.'
    binding = inspect_evidence_binding('Configuration?', raw)
    assert [finding['assessment_index'] for finding in binding['findings']] == [0]


def test_literal_presence_in_hypothetical_source_does_not_certify_config_entailment():
    raw = config(quotes=('If max_retries=2, that is an example only.',))
    binding = inspect_evidence_binding('Configuration?', raw)
    # The token is present, but this document does NOT establish a real setting.
    # The guard is explicitly not a classifier of quoted authority or modality.
    assert binding['findings'] == []
    assert binding['assessments'][0]['configuration']['checks'][0]['state'] == 'checked_literal_presence'
    assert binding['semantic_entailment_verified'] is False
    assert binding['source_authority_verified'] is False


@pytest.mark.parametrize('value', ['2+1', '2 / 3', '2,000', '2, 000', '2-1', '2**3',
                                   '2.0+1', '2e0 / 3', '2×3', '2−1', '2 ± 1'])
def test_expression_or_grouped_integer_claim_is_unverified_not_prefix_checked(value):
    binding = inspect_evidence_binding('Configuration?', config(
        explanation=f'max_retries={value}', quotes=('max_retries=2',)))
    assert binding['findings'] == []
    check = binding['assessments'][0]['configuration']
    assert check['state'] == 'unverified' and check['checks'] == []


@pytest.mark.parametrize('value', ['2+1', '2 / 3', '2,000', '2, 000', '2-1', '2**3',
                                   '2.0+1', '2e0 / 3', '2×3', '2−1', '2 ± 1'])
def test_expression_or_grouped_integer_source_cannot_license_direct_scalar(value):
    binding = inspect_evidence_binding('Configuration?', config(quotes=(f'max_retries={value}',)))
    assert [finding['rule'] for finding in binding['findings']] == ['configuration_literal_not_in_selected_quotes']


@pytest.mark.parametrize(('prose', 'quote'), [
    ('max_retries=2.', 'max_retries=2.'),
    ('max_retries=2.0。', 'max_retries: 2.0。'),
    ('max_retries=2e0;', 'max_retries=2.0e+0;'),
    ('max_retries=2, batch_size=4.', 'max_retries=2, batch_size=4.'),
    ('test_value=-2.', 'test_value=-2.0.'),
    ('test_value=2e-1.', 'test_value=0.2.'),
])
def test_complete_simple_scalar_decimal_and_scientific_tokens_keep_punctuation(prose, quote):
    binding = inspect_evidence_binding('Configuration?', config(explanation=prose, quotes=(quote,)))
    assert binding['findings'] == []
    checks = binding['assessments'][0]['configuration']['checks']
    assert checks and all(row['state'] == 'checked_literal_presence' for row in checks)
