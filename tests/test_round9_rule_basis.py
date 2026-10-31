"""Rule classification contracts and immutable failure replay, no model evaluation."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lablens.assessment import (
    ASSESSMENT_INSTRUCTIONS,
    SELECTION_INSTRUCTIONS,
    RULE_EVALUATION_INSTRUCTIONS,
    Assessment,
    AssessmentSelection,
    evidence_catalog,
    resolve_selections,
    selection_schema,
    validate_assessments,
)
from lablens.rag import _restore_review_statements, _scoped_selection_schema

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'docs/evaluations/conversation-round8/runs/round8-once/cedar-t1-final-task.json'
BASES = ['documented_fact', 'current_configuration', 'historical_observation',
         'causal_claim', 'rule_evaluation']


def rule_case(**changes):
    """Different subject, rule, units and thresholds from the exposed Cedar case."""
    texts = [
        ('measurements', 'Trial R: Quartz elapsed 47 ms; 3 failures in 800 requests. Local synthetic run.'),
        ('rule', 'Rule R requires BOTH elapsed <= 50 ms AND failure proportion <= 0.5 percent. Equality passes.'),
    ]
    hits = [dict(chunk_id=cid, document_id=cid, title=cid, source=cid + '.md', text=text,
                 start=0, end=len(text), content_sha256=hashlib.sha256(text.encode()).hexdigest())
            for cid, text in texts]
    row = dict(statement='Quartz passes rule R in this local synthetic run.', verdict='supported',
               explanation='47 <= 50 ms and 3 / 800 = 0.375% <= 0.5%; both requirements hold.',
               basis='rule_evaluation', citation_ids=[hit['chunk_id'] for hit in hits],
               quotes=[dict(chunk_id=hit['chunk_id'], quote=hit['text']) for hit in hits])
    row.update(changes)
    return hits, row


@pytest.mark.parametrize('basis', BASES)
def test_report_and_selection_roundtrip_keep_old_basis_values_and_new_rule_value(basis):
    hits, row = rule_case(basis=basis)
    assert Assessment.model_validate(row).model_dump() == row
    selection = {k: v for k, v in row.items() if k not in {'citation_ids', 'quotes'}}
    selection['evidence_ids'] = list(evidence_catalog(hits))
    assert AssessmentSelection.model_validate(selection).model_dump() == selection
    assert resolve_selections({'assessments': [selection]}, hits)['assessments'] == [row]


def test_all_generation_schema_paths_expose_same_compatible_basis_enum():
    hits, row = rule_case()
    schemas = [
        (Assessment.model_json_schema(), 'properties'),
        (AssessmentSelection.model_json_schema(), 'properties'),
        (selection_schema(hits)['$defs']['AssessmentSelection'], 'properties'),
        (_scoped_selection_schema(hits, [row['statement']])['$defs']['AssessmentSelection'], 'properties'),
    ]
    for schema, key in schemas:
        assert schema[key]['basis']['enum'] == BASES


@pytest.mark.parametrize('unknown', ['acceptance', 'causal_supported', '', None])
def test_unknown_basis_is_still_rejected(unknown):
    _, row = rule_case(basis=unknown)
    with pytest.raises(ValidationError):
        Assessment.model_validate(row)


@pytest.mark.parametrize('verdict', ['supported', 'contradicted', 'insufficient'])
def test_rule_result_can_retain_any_verdict_without_becoming_causal_certification(verdict):
    hits, row = rule_case(verdict=verdict)
    result = validate_assessments('Assess rule R.', {'assessments': [row]}, hits)
    assert result['assessments'] == [row]
    assert result['assessment_validation']['adjustments'] == []
    assert result['assessment_validation']['semantic_entailment_verified'] is False
    assert result['assessment_validation']['causal_certification'] is False


def test_scoped_reviewer_can_explicitly_correct_type_without_replacing_statement():
    hits, row = rule_case()
    # This is a simulated review response, not an automatic server rewrite.
    reviewed = {'assessments': [dict(assessment_id='a1', verdict='supported',
        explanation=row['explanation'], basis='rule_evaluation', evidence_ids=list(evidence_catalog(hits)))]}
    restored = _restore_review_statements(reviewed, [row['statement']])
    resolved = resolve_selections(restored, hits)
    assert resolved['assessments'] == [row]
    assert validate_assessments('Assess rule R.', resolved, hits)['assessments'] == [row]


@pytest.mark.parametrize(('statement', 'explanation'), [
    ('The configuration caused the measured latency improvement.', 'Both rule conditions hold.'),
    ('该配置导致延迟下降。', '符合规则中的门槛。'),
    ('Quartz passes rule R.', 'The cache is the root cause of the improvement.'),
    ('Quartz 通过规则 R。', '缓存是延迟下降的根本原因。'),
])
def test_rule_label_cannot_bypass_existing_causal_language_guard(statement, explanation):
    hits, row = rule_case(statement=statement, explanation=explanation)
    result = validate_assessments('Assess rule R.', {'assessments': [row]}, hits)
    assert result['assessments'][0]['basis'] == 'rule_evaluation'
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessment_validation']['adjustments'][-1]['rule'] == 'causal_claim_requires_external_controlled_review'


def test_causal_label_is_not_silently_corrected_even_when_prose_is_a_rule_result():
    hits, row = rule_case(basis='causal_claim')
    original = copy.deepcopy(row)
    result = validate_assessments('Assess rule R.', {'assessments': [row]}, hits)
    assert row == original
    assert result['assessments'][0]['basis'] == 'causal_claim'
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessment_validation']['adjustments'][-1]['rule'] == 'causal_claim_requires_external_controlled_review'


def test_rule_label_cannot_turn_config_only_history_into_an_observation():
    hits, row = rule_case(statement='The job executed retries in the historical run.',
                          explanation='The config value establishes runtime execution.')
    hits[0]['source'] = 'configuration.yaml'
    row['citation_ids'] = [hits[0]['chunk_id']]
    row['quotes'] = [row['quotes'][0]]
    result = validate_assessments('Assess history.', {'assessments': [row]}, hits)
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessment_validation']['adjustments'][-1]['rule'] == 'config_code_cannot_establish_historical_or_causal_fact'


def test_rule_evaluation_cannot_be_supported_without_any_selected_evidence():
    hits, row = rule_case(citation_ids=[], quotes=[])
    with pytest.raises(ValueError, match='requires evidence'):
        validate_assessments('Assess rule R.', {'assessments': [row]}, hits)
    row.update(verdict='insufficient', explanation='The applicable rule and trial measurements were not supplied.')
    assert validate_assessments('Assess rule R.', {'assessments': [row]}, hits)['assessments'] == [row]


def test_shared_draft_and_review_instruction_requires_both_premises_in_each_rule_row():
    for instructions in [ASSESSMENT_INSTRUCTIONS, SELECTION_INSTRUCTIONS]:
        assert instructions.count(RULE_EVALUATION_INSTRUCTIONS) == 1
        assert "EACH such row select the exact subject's measurements AND its applicable rule" in instructions
        assert "Another row's citations cannot supply these premises" in instructions
        assert 'bounds, equality, AND/OR logic and scope' in instructions
        assert 'mechanism explanations and keep them insufficient' in instructions
    assert len(RULE_EVALUATION_INSTRUCTIONS.encode()) < 900


def test_frozen_cedar_draft_preserves_original_failure_and_isolates_new_basis_on_copies():
    before = RAW.read_bytes()
    saved = json.loads(before)
    result = saved['result']
    draft = result['agent']['trace'][16]['arguments']['assessments']
    original = {'assessments': copy.deepcopy(draft[4:6])}
    assert [row['basis'] for row in original['assessments']] == ['causal_claim', 'causal_claim']
    hits = result['citations']
    resolved = resolve_selections(original, hits)
    failure = validate_assessments(result['question'], resolved, hits)
    assert [row['verdict'] for row in failure['assessments']] == ['insufficient', 'insufficient']
    assert all(change['rule'] == 'causal_claim_requires_external_controlled_review'
               for change in failure['assessment_validation']['adjustments'])
    corrected = copy.deepcopy(resolved)
    for row in corrected['assessments']:
        row['basis'] = 'rule_evaluation'
    replay = validate_assessments(result['question'], corrected, hits)
    assert [row['verdict'] for row in replay['assessments']] == ['supported', 'supported']
    # Both original overall rows select the measured values and approved rule.
    for row in replay['assessments']:
        text = ' '.join(quote['quote'] for quote in row['quotes'])
        assert 'Baseline consumed 240 kWh' in text and 'passes C-21 only if BOTH conditions hold' in text
    assert original['assessments'] == draft[4:6]
    assert RAW.read_bytes() == before


def test_frozen_measurement_only_threshold_rows_remain_an_explicit_semantic_gap():
    """Exact quotes cannot certify arbitrary missing normative premises.

    Keep the known failing form visible instead of mistaking a basis enum and
    prompt requirement for a universal source-entailment checker.
    """
    before = RAW.read_bytes()
    saved = json.loads(before)['result']
    rows = copy.deepcopy(saved['agent']['trace'][16]['arguments']['assessments'][2:4])
    for row in rows:
        row['basis'] = 'rule_evaluation'
    resolved = resolve_selections({'assessments': rows}, saved['citations'])
    checked = validate_assessments(saved['question'], resolved, saved['citations'])
    assert all(len(row['citation_ids']) == 1 for row in checked['assessments'])
    assert checked['assessment_validation']['semantic_entailment_verified'] is False
    assert all('passes C-21 only if' not in quote['quote']
               for row in checked['assessments'] for quote in row['quotes'])
    assert RAW.read_bytes() == before
