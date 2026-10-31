"""Bounded arithmetic dependency regressions, never model quality scores."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from lablens.answer_quality import enforce_review_checks, make_review_contract, selected_evidence_findings
from lablens.evidence_derivations import inspect_report_derivations


READINGS = 'Baseline consumed 240 kWh. Dune consumed 208 kWh. Reed consumed 210 kWh.'
RULE = ('Use (baseline energy minus candidate energy) divided by baseline energy, times 100. '
        'Energy reduction must be at least 12.5 percent.')


def quote(chunk_id, text):
    return dict(chunk_id=chunk_id, quote=text)


def row(statement, explanation, quotes, **changes):
    value = dict(statement=statement, explanation=explanation, verdict='supported',
        basis='historical_observation', citation_ids=list(dict.fromkeys(q['chunk_id'] for q in quotes)), quotes=quotes)
    value.update(changes)
    return value


def report():
    return dict(assessments=[
        row('Dune 的节电百分比为 (240 - 208) / 240 × 100 = 13.3%',
            'Baseline 为 240 kWh，Dune 为 208 kWh，(240 - 208) / 240 × 100 = 13.3%。', [quote('measured', READINGS)]),
        row('Dune 未通过验收', '虽节电 13.3% ≥ 12.5% 达标，但另一项未达标。',
            [quote('measured', READINGS), quote('rules', RULE)], basis='rule_evaluation')])


def hits(raw, *, source='measurements.md'):
    texts = {}
    for r in raw['assessments']:
        for q in r['quotes']:
            texts.setdefault(q['chunk_id'], set()).add(q['quote'])
    return [dict(chunk_id=cid, text='\n'.join(sorted(values)), document_id=cid,
        title=cid, source=source, start=0, end=1,
        content_sha256=hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest())
        for cid, values in texts.items()]


def inspect(raw=None, **kwargs):
    raw = raw or report()
    return inspect_report_derivations(raw, producer_eligible_rows=set(range(len(raw['assessments']))), **kwargs)


def enforce(raw=None, **kwargs):
    raw = raw or report()
    return enforce_review_checks('评估 Dune 和 Reed。', raw, raw, hits=hits(raw), **kwargs)


def assert_not_reused(raw):
    result = inspect(raw)
    assert result.allowed_spans == {}
    assert result.audit['dependencies'] == []
    return result


def test_same_subject_same_exact_operands_and_selected_rule_reuse_without_equation_copy():
    raw = report()
    before = copy.deepcopy(raw)
    assert selected_evidence_findings(raw)[0]['quantities'] == ['13.3%']
    protected, quality = enforce(raw)
    assert quality['findings'] == []
    assert protected == before == raw
    dependency = quality['derivation_dependencies']['dependencies'][0]
    assert dependency['producer_assessment_index'] == 0
    assert dependency['consumer_assessment_index'] == 1
    assert dependency['subject'] == 'dune'
    assert dependency['consumer_quantity'] == '13.3%'
    assert dependency['quantity_type'] == 'energy_relative_reduction'
    assert dependency['operand_sources'] == [dict(chunk_id='measured', quote_sha256=hashlib.sha256(READINGS.encode()).hexdigest())]
    assert dependency['consumer_rule_sources'][0]['chunk_id'] == 'rules'
    audit = quality['derivation_dependencies']
    assert audit['source_authenticity_verified'] is False
    assert audit['semantic_entailment_verified'] is False
    assert audit['acceptance_verified'] is False
    assert audit['transitive_dependencies_allowed'] is False


def test_without_hit_metadata_final_validation_is_unavailable_and_guard_stays_conservative():
    raw = report()
    protected, quality = enforce_review_checks('Dune', raw, raw)
    assert protected['assessments'][1]['verdict'] == 'insufficient'
    assert quality['derivation_dependencies']['certificates'] == []
    assert quality['derivation_dependencies']['unverified'] == [dict(reason='producer_final_validation_unavailable')]
    assert make_review_contract('Dune', raw)['derivation_dependencies']['state'] == 'unverified'


def test_frozen_cedar_overall_row_reuses_original_formula_without_changing_original_input():
    path = Path('docs/evaluations/conversation-round8/runs/round8-once/cedar-t1-final-task.json')
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    result = json.loads(path.read_text())['result']
    raw = dict(assessments=copy.deepcopy(result['assessments']))
    original = result['assessment_validation']['selected_evidence_checks']['withheld_originals'][0]
    raw['assessments'][4]['verdict'] = original['original_verdict']
    raw['assessments'][4]['explanation'] = original['original_explanation']
    before = copy.deepcopy(raw)
    assert any(f['assessment_index'] == 4 for f in selected_evidence_findings(raw))
    protected, quality = enforce_review_checks(result['question'], raw, raw, hits=result['citations'])
    assert not any(f['assessment_index'] == 4 for f in quality['findings'])
    assert protected['assessments'][4] == before['assessments'][4]
    assert quality['derivation_dependencies']['dependencies'][0]['consumer_assessment_index'] == 4
    # This fixes only the numeric guard; the recorded causal label remains and
    # the later validator still withholds it. Do not silently relabel the row.
    from lablens.assessment import validate_assessments
    final = validate_assessments(result['question'], protected, result['citations'])
    assert final['assessments'][4]['verdict'] == 'insufficient'
    assert raw == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash


@pytest.mark.parametrize('changed', [
    'Reed 未通过验收', 'Unknown 未通过验收', 'Dune-2 未通过验收', 'Dune 和 Reed 未通过验收', '该对象未通过验收'])
def test_subject_is_required_in_consumer_proposition(changed):
    raw = report()
    raw['assessments'][1]['statement'] = changed
    assert_not_reused(raw)


@pytest.mark.parametrize('explanation', [
    'Reed 节电 13.3%，Dune 未通过。', 'Dune 延迟降低 13.3%。', 'Dune memory reduction 13.3%。',
    'Dune 比例为 13.3%。', '节电 13.4%，未通过。', '节电 13.30% 且节电 13.3%。',
    '如果节电 13.3% 就通过。', '假设节电 13.3%。', '节电可能为 13.3%。',
    '节电不是 13.3%。', '节电 13.3% 是错误的，实际未知。',
    'Energy reduction is not 13.3%.', 'Energy reduction 13.3% if the baseline is correct.',
    '节电 13.3% 导致通过验收。'])
def test_wrong_type_subject_conflict_qualified_or_bad_result_is_not_reused(explanation):
    raw = report()
    raw['assessments'][1]['explanation'] = explanation
    assert_not_reused(raw)


@pytest.mark.parametrize('where', [0, 1])
@pytest.mark.parametrize('text', [
    'If Baseline consumed 240 kWh. Dune consumed 208 kWh. Reed consumed 210 kWh.',
    'Baseline consumed 240 kWh. Dune consumed 208 kWh if testing succeeds.',
    'Baseline consumed 240 kWh. Not Dune consumed 208 kWh.',
    'Baseline consumed 240 kWh. Dune consumed 208 kWh. Dune consumed 210 kWh.',
    'Baseline consumed 240 kWh. Baseline consumed 242 kWh. Dune consumed 208 kWh.',
    'Baseline consumed 240 kWh. Dune consumed 208 Wh.',
    'Baseline consumed 240 kWh. Reed consumed 208 kWh.'])
def test_conditional_conflicting_mismatched_operand_quotes_do_not_license_reuse(where, text):
    raw = report()
    raw['assessments'][where]['quotes'][0]['quote'] = text
    assert_not_reused(raw)


@pytest.mark.parametrize('change', ['same_numbers_new_chunk', 'same_chunk_other_excerpt', 'missing_measurement', 'missing_rule',
    'same_chunk_fake_rule', 'conditional_rule', 'negated_rule', 'wrong_formula_rule', 'uncited_quote'])
def test_exact_operand_and_rule_identity_are_required(change):
    raw = report()
    consumer = raw['assessments'][1]
    if change == 'same_numbers_new_chunk':
        consumer['quotes'][0]['chunk_id'] = 'other-run'
        consumer['citation_ids'][0] = 'other-run'
    elif change == 'same_chunk_other_excerpt':
        consumer['quotes'][0]['quote'] = READINGS + ' A different selected excerpt.'
    elif change == 'missing_measurement':
        consumer['quotes'].pop(0)
        consumer['citation_ids'].pop(0)
    elif change == 'missing_rule':
        consumer['quotes'].pop(1)
        consumer['citation_ids'].pop(1)
    elif change == 'same_chunk_fake_rule':
        consumer['quotes'][1]['quote'] = 'The energy reduction threshold is 12.5 percent.'
    elif change == 'conditional_rule':
        consumer['quotes'][1]['quote'] = 'If applicable, ' + RULE
    elif change == 'negated_rule':
        consumer['quotes'][1]['quote'] = 'Do not ' + RULE
    elif change == 'wrong_formula_rule':
        consumer['quotes'][1]['quote'] = RULE.replace('divided by baseline', 'divided by candidate')
    else:
        consumer['citation_ids'].remove('measured')
    assert_not_reused(raw)


def test_rule_already_selected_by_producer_must_keep_its_exact_identity():
    raw = report()
    raw['assessments'][0]['quotes'].append(quote('rule-original', RULE))
    raw['assessments'][0]['citation_ids'].append('rule-original')
    assert_not_reused(raw)
    raw['assessments'][1]['quotes'][1]['chunk_id'] = 'rule-original'
    raw['assessments'][1]['citation_ids'][1] = 'rule-original'
    assert len(inspect(raw).audit['dependencies']) == 1


@pytest.mark.parametrize('change', ['insufficient', 'contradicted', 'causal', 'config', 'bad_equation', 'unknown_number',
    'conditional', 'negated', 'causal_language'])
def test_nonqualifying_producer_never_lends_a_number(change):
    raw = report()
    producer = raw['assessments'][0]
    if change in {'insufficient', 'contradicted'}:
        producer['verdict'] = change
    elif change in {'causal', 'config'}:
        producer['basis'] = {'causal': 'causal_claim', 'config': 'current_configuration'}[change]
    elif change == 'bad_equation':
        producer['statement'] = producer['statement'].replace('/ 240', '/ 208')
        producer['explanation'] = producer['explanation'].replace('/ 240', '/ 208')
    elif change == 'unknown_number':
        producer['explanation'] += ' Unknown latency 81 ms.'
    elif change == 'conditional':
        producer['explanation'] = 'If measured, ' + producer['explanation']
    elif change == 'negated':
        producer['explanation'] = 'Not ' + producer['explanation']
    else:
        producer['explanation'] += ' This caused acceptance.'
    protected, quality = enforce(raw)
    assert quality['derivation_dependencies']['dependencies'] == []
    assert protected['assessments'][1]['verdict'] == 'insufficient'


def test_producer_blocked_by_source_metadata_cannot_license_an_otherwise_eligible_consumer():
    raw = report()
    protected, quality = enforce_review_checks('Dune', raw, raw, hits=hits(raw, source='config.yaml'))
    assert quality['derivation_dependencies']['certificates'] == []
    assert protected['assessments'][1]['verdict'] == 'insufficient'


def test_explicit_scope_or_binding_block_prevents_certificate():
    result = inspect(report(), blocked_rows={0})
    assert result.audit['certificates'] == [] and result.allowed_spans == {}


def test_consumer_does_not_get_a_global_license_for_the_same_ratio_elsewhere():
    raw = report()
    raw['assessments'][1]['explanation'] += ' Another probability is 13.3%.'
    protected, quality = enforce(raw)
    assert len(quality['derivation_dependencies']['dependencies']) == 1
    assert quality['findings'][0]['quantities'] == ['13.3%']
    assert protected['assessments'][1]['verdict'] == 'insufficient'


def test_no_equation_cyclic_or_chained_rows_can_bootstrap_derivations():
    raw = report()
    raw['assessments'][0] = copy.deepcopy(raw['assessments'][1])
    raw['assessments'][0]['statement'] = 'Dune 节电结论一'
    raw['assessments'][0]['depends_on'] = [1]  # Not an accepted model contract.
    raw['assessments'][1]['depends_on'] = [0]
    assert_not_reused(raw)


def test_row_order_is_irrelevant_but_dependencies_remain_one_hop():
    raw = report()
    raw['assessments'].reverse()
    result = inspect(raw)
    assert result.audit['dependencies'][0]['consumer_assessment_index'] == 0
    assert result.audit['dependencies'][0]['producer_assessment_index'] == 1


@pytest.mark.parametrize('change', ['prose', 'quotes', 'row_count', 'bad_formula', 'huge_number'])
def test_bounded_inputs_do_not_issue_dependencies(change):
    raw = report()
    if change == 'prose':
        raw['assessments'][0]['explanation'] += 'a' * 32_001
    elif change == 'quotes':
        raw['assessments'][0]['quotes'][0]['quote'] += 'a' * 128_001
    elif change == 'row_count':
        raw['assessments'] *= 9
    elif change == 'bad_formula':
        raw['assessments'][0]['statement'] = 'Dune energy reduction (240 + 208) / 240 * 100 = 13.3%'
        raw['assessments'][0]['explanation'] = 'No valid equation.'
    else:
        raw['assessments'][0]['statement'] = 'Dune energy reduction (240 - 208) / 240 * 100 = 1e999999%'
        raw['assessments'][0]['explanation'] = 'Explicit equation only.'
    assert_not_reused(raw)


def test_general_names_and_energy_values_are_not_cedar_specific():
    raw = report()
    for r in raw['assessments']:
        for key in ('statement', 'explanation'):
            r[key] = r[key].replace('Dune', 'Orchid').replace('240', '500').replace('208', '400').replace('13.3', '20.0')
        for q in r['quotes']:
            q['quote'] = q['quote'].replace('Dune', 'Orchid').replace('240', '500').replace('208', '400')
    assert inspect(raw).audit['dependencies'][0]['subject'] == 'orchid'


def test_nonenergy_units_remain_explicitly_outside_new_reuse_scope():
    raw = report()
    for r in raw['assessments']:
        for q in r['quotes']:
            q['quote'] = q['quote'].replace('kWh', 'ms')
    assert_not_reused(raw)


@pytest.mark.parametrize('change', [' + 1', ' / 2', ' ** 3', '.5', ',000', ')'])
def test_formula_prefix_cannot_be_promoted_from_a_larger_or_malformed_expression(change):
    raw = report()
    for key in ('statement', 'explanation'):
        raw['assessments'][0][key] = raw['assessments'][0][key].replace('13.3%', '13.3%' + change)
    assert_not_reused(raw)


def test_formula_inside_multiplication_cannot_be_promoted_as_a_complete_derivation():
    raw = report()
    for key in ('statement', 'explanation'):
        raw['assessments'][0][key] = raw['assessments'][0][key].replace('(240', '2 * (240')
    assert_not_reused(raw)
