"""Local grammar/regression tests, not model-answer accuracy measurements."""
import json
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from lablens.evidence_arithmetic import inspect_percentage_derivations


READINGS = ('Baseline consumed 240 kWh. Dune consumed 208 kWh; its maximum interior '
            'temperature was 5.2 degrees Celsius. Reed consumed 210 kWh.')
RULE = ('Use (baseline energy minus candidate energy) divided by baseline energy, times 100. '
        'Energy reduction must be at least 12.5 percent.')
GOOD = 'Dune的节电百分比为(240-208)/240×100=13.3%，超过12.5%的阈值。'


def inspect(prose=GOOD, quotes=READINGS + '\n' + RULE):
    return inspect_percentage_derivations(prose, quotes)


@pytest.mark.parametrize(('prose', 'value'), [
    (GOOD, '0.133'),
    ('Dune energy reduction: (240 - 208) / 240 * 100 ≈ 13.33%.', '0.1333'),
    ('Dune节电率=(240 kWh−208 kWh)÷240 kWh×100等于13.333 percent。', '0.13333'),
    ('Dune energy savings: (240-208)/240*100=13%.', '0.13'),
    ('Reed的节电率=(240-210)/240*100=12.5%。', '0.125'),
    ('Baseline能耗240 kWh，Dune能耗208 kWh，节电率=(240-208)/240×100=13.3%。', '0.133'),
])
def test_explicit_baseline_reduction_is_derived_with_bounded_rounding(prose, value):
    result = inspect(prose)
    assert result.derived_quantities == {('ratio', Decimal(value))}
    assert result.findings == ()
    assert result.accepted[0]['unit'] == 'kwh'
    assert result.accepted[0]['scope'].startswith('literal selected-quote operands')


@pytest.mark.parametrize(('prose', 'quotes', 'reason'), [
    ('Dune节电率=(240-208)/208*100=15.4%。', READINGS, 'denominator_is_not_baseline'),
    ('Dune节电率=(240-208)/240*100=12.5%。', READINGS + RULE, 'result_incorrect'),
    ('Dune节电率=(240-210)/240*100=12.5%。', READINGS + RULE, 'operand_subject_mismatch'),
    ('Reed节电率=(240-208)/240*100=13.3%。', READINGS, 'operand_subject_mismatch'),
    ('Dune与Reed节电率=(240-208)/240*100=13.3%。', READINGS, 'candidate_subject_missing_or_ambiguous'),
    ('Unknown节电率=(240-208)/240*100=13.3%。', READINGS, 'candidate_subject_missing_or_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. Reed consumed 208 kWh.', 'candidate_subject_missing_or_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. Dune consumed 208 W.', 'operand_unit_mismatch'),
    (GOOD, 'Baseline consumed 240 kWh. Dune consumed 208 Wh.', 'operand_unit_mismatch'),
    ('Dune节电率=(240 W-208 W)/240 W*100=13.3%。', READINGS, 'operand_unit_mismatch'),
    ('Dune节电率=(240-208)/240*10=1.3%。', READINGS, 'scale_is_not_100'),
    ('Dune节电率=(240-)/240*100=12.5%。', READINGS + RULE, 'expression_outside_bounded_grammar'),
    ('Dune节电率=(240+208)/240*100=186.7%。', READINGS, 'expression_outside_bounded_grammar'),
    ('Dune节电率=(240-208)/240*100=13.4%。', READINGS, 'result_incorrect'),
    ('Dune节电率=(240-208)/240*100=13.34%。', READINGS, 'result_incorrect'),
    ('Dune率=(240-208)/240*100=13.3%。', READINGS, 'reduction_scope_not_explicit'),
    (GOOD, 'Dune consumed 208 kWh. The rule mentions 240.', 'baseline_measurement_missing_or_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. The number 208 is reported.', 'candidate_subject_missing_or_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. Dune consumed 208 kWh. Dune consumed 210 kWh.', 'candidate_measurement_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. Baseline consumed 242 kWh. Dune consumed 208 kWh.', 'baseline_measurement_missing_or_ambiguous'),
    (GOOD, 'Baseline consumed 240 kWh. Not Dune consumed 208 kWh.', 'candidate_subject_missing_or_ambiguous'),
    ('Dune节电率=(0-208)/0*100=13.3%。', 'Baseline consumed 0 kWh. Dune consumed 208 kWh.', 'nonpositive_baseline_or_negative_measurement'),
    ('Dune节电率=(240--208)/240*100=186.7%。', 'Baseline consumed 240 kWh. Dune consumed -208 kWh.', 'nonpositive_baseline_or_negative_measurement'),
    ('Dune节电率=(240-208)/240*100=1e9999999%。', READINGS, 'quantity_outside_bounded_parser'),
])
def test_invalid_or_unbound_chain_is_not_accepted_even_when_result_is_quoted(prose, quotes, reason):
    result = inspect(prose, quotes)
    assert result.derived_quantities == frozenset()
    assert result.accepted == ()
    assert result.findings[0]['rule'] == 'percentage_' + reason


def test_same_fact_repeated_in_two_selected_quotes_is_not_a_conflict():
    result = inspect(quotes=READINGS + '\n' + READINGS)
    assert result.findings == () and len(result.accepted) == 1


def test_subject_tokens_do_not_match_inside_another_identifier():
    assert inspect(GOOD.replace('Dune', 'Dune-2')).findings[0]['rule'] == 'percentage_candidate_subject_missing_or_ambiguous'


def test_new_names_values_and_units_are_not_hardcoded_to_cedar():
    result = inspect('Orchid latency reduction: (1,250-1,000)/1,250*100=20%.',
        'Baseline latency was 1,250 ms. Orchid measured 1,000 ms.')
    assert result.derived_quantities == {('ratio', Decimal('.2'))}
    assert result.accepted[0]['subject'] == 'orchid'
    assert result.accepted[0]['unit'] == 'ms'


def test_chinese_measurement_markers_and_unit_aliases():
    result = inspect('Dune节电率=(240千瓦时-208千瓦时)/240千瓦时*100=13.3%。',
        '基线能耗240 kWh，Dune能耗208千瓦时。')
    assert result.derived_quantities == {('ratio', Decimal('.133'))}


def test_negative_reduction_is_mathematically_valid_without_claiming_pass():
    result = inspect('Dune energy reduction: (240-264)/240*100=-10%.',
        'Baseline consumed 240 kWh. Dune consumed 264 kWh.')
    assert result.derived_quantities == {('ratio', Decimal('-.1'))}


def test_decimal_precision_is_local_and_does_not_change_global_context():
    with localcontext() as context:
        context.prec = 5
        result = inspect()
        assert context.prec == 5
    assert result.derived_quantities == {('ratio', Decimal('.133'))}


def test_code_like_expression_is_rejected_without_execution(tmp_path):
    marker = tmp_path / 'must-not-exist'
    # The parser is a literal grammar, not eval, compile, a tool call or Python AST execution.
    result = inspect(f'Dune节电率=(open({str(marker)!r},"w"))/240*100=13.3%。')
    assert result.derived_quantities == frozenset()
    assert not marker.exists()


@pytest.mark.parametrize('prose', [
    'Dune节电率13.3%。',  # No equation: do not invent a derivation.
    'Dune节电率=32/240*100=13.3%。',  # No explicit source-grounded subtraction.
    'Dune节电率=(240-208)*100/240=13.3%。',  # Alternative grammar deliberately not claimed.
    'Dune节电率=(240-208)/240*100=NaN%。',
    'Dune节电率=(240-208)/240*100=Infinity%。',
    'Dune节电率=(' + '9' * 500 + '-208)/240*100=13.3%。',
])
def test_unrecognized_forms_never_gain_a_derived_anchor(prose):
    assert inspect(prose).derived_quantities == frozenset()


def test_bounded_input_and_expression_counts_fail_closed():
    assert inspect('a' * 32_001).findings[0]['rule'] == 'percentage_input_limit_exceeded'
    assert inspect(quotes='a' * 128_001).findings[0]['rule'] == 'percentage_input_limit_exceeded'
    result = inspect('\n'.join([GOOD] * 17))
    assert result.derived_quantities == frozenset()
    assert result.findings[0]['rule'] == 'percentage_expression_limit_exceeded'


def test_immutable_round5_cedar_failed_assessment_now_has_a_numeric_derivation():
    path = Path('docs/evaluations/conversation-round5/development/grading/failure-diagnostics.json')
    raw = json.loads(path.read_text())
    # Read the recorded provider draft, rather than constructing only a matching fixture.
    def assessments(value):
        if isinstance(value, dict):
            if isinstance(value.get('quotes'), list) and 'statement' in value and 'explanation' in value:
                yield value
            for child in value.values():
                yield from assessments(child)
        elif isinstance(value, list):
            for child in value:
                yield from assessments(child)
    candidates = [item for item in assessments(raw) if 'Dune的节电百分比' in item['statement'] and item['quotes']]
    assert candidates
    item = candidates[0]
    result = inspect(item['statement'] + '\n' + item['explanation'], '\n'.join(q['quote'] for q in item['quotes']))
    assert result.findings == ()
    assert result.derived_quantities == {('ratio', Decimal('.133'))}
