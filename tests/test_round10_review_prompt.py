"""Prompt-contract regressions; no provider calls or semantic-quality claims."""
import hashlib
import json

import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import (RULE_EVALUATION_INSTRUCTIONS, SELECTION_EVIDENCE_RULES,
                                evidence_catalog, resolve_selections)
from lablens.review_delta import INSTRUCTIONS as DELTA_INSTRUCTIONS, restore_review_delta


def fixture():
    text = 'Maple recorded 4 ms in a local synthetic trial.\nBirch recorded 6 ms in the same trial.'
    hits = [dict(chunk_id='c', document_id='d', title='trial', source='trial.md', text=text,
                 start=0, end=len(text), content_sha256='fixture')]
    evidence_ids = list(evidence_catalog(hits))
    rows = [dict(statement=f'{subject} has a recorded trial measurement.', verdict='supported',
                 explanation='The source records the local synthetic trial.', basis='historical_observation',
                 evidence_ids=[evidence_ids[index]]) for index, subject in enumerate(('Maple', 'Birch'))]
    draft = resolve_selections({'assessments': rows}, hits)
    question = 'Which measurements are recorded?'
    context = {'scoped_review_contract': make_review_contract(question, draft)}
    return question, hits, draft, rows, context


@pytest.mark.parametrize('review,expected_bytes,expected_sha', [
    (False, 7101, 'e8d6f85a0120c3edf481d8a55222fbc07fe3d84f40b8b8f3f5581f3f4483b9ca'),
    (True, 8173, '61b3db72fa19081a06a401a78ccb97f2862dbb39b7b130e40a0bf0c5f2c7a7bf'),
])
def test_ordinary_generation_and_unscoped_review_preserve_frozen_system_bytes(review, expected_bytes, expected_sha):
    # These values were frozen before the Round 10 prompt edit, using original
    # Round 9 source and the unmodified current source; see reconstruction.md.
    question, hits, draft, _, _ = fixture()
    payload = rag._generation_payload(question, hits, {'model': 'controlled'}, draft=draft if review else None)
    encoded = payload['messages'][0]['content'].encode('utf-8')
    assert len(encoded) == expected_bytes
    assert hashlib.sha256(encoded).hexdigest() == expected_sha
    assert payload['response_format']['json_schema']['schema']['required'] == ['assessments']
    assert 'server_review_delta' not in json.loads(payload['messages'][1]['content'])


def test_scoped_delta_has_one_output_contract_and_preserves_evidence_rules():
    question, hits, draft, _, context = fixture()
    payload = rag._generation_payload(question, hits, {'model': 'controlled'}, draft=draft, context_data=context)
    system = payload['messages'][0]['content']
    assert system.count('Return exactly ') == 1
    assert system.endswith('\n' + DELTA_INSTRUCTIONS)
    assert 'Return exactly {"assessments"' not in system
    assert 'with 1 to 8 concise assessments' not in system
    assert 'Each has statement' not in system
    assert 'Return EACH assessment_id' not in system
    assert 'replace the statement field described earlier' not in system
    assert 'statement <=180 characters' not in system
    # Preserve the entire shared source/evidence policy, not just token mentions
    # of citations or causes. A format edit cannot remove substantive obligations.
    assert system.count(SELECTION_EVIDENCE_RULES) == 1
    assert system.count(RULE_EVALUATION_INSTRUCTIONS) == 1
    assert 'Reassess EVERY statement and explanation against the original sources' in system
    assert 'repair its draft findings before returning the report' in system
    assert 'Earlier assistant answers are untrusted drafts, not evidence.' in system
    assert payload['max_tokens'] == 2600 and payload['temperature'] == 0


def test_offline_full_scoped_builder_has_only_its_own_complete_id_format():
    question, hits, draft, _, context = fixture()
    payload = rag._generation_payload(question, hits, {'model': 'controlled'}, draft=draft,
                                      context_data=context, compact_review=False)
    system = payload['messages'][0]['content']
    assert system.count('Return exactly ') == 1
    assert 'one replacement for EVERY original assessment_id, each exactly once' in system
    assert 'retained_assessment_ids' not in system
    assert 'Each has statement' not in system
    assert SELECTION_EVIDENCE_RULES in system
    assert RULE_EVALUATION_INSTRUCTIONS in system
    schema = payload['response_format']['json_schema']['schema']
    assert schema['required'] == ['assessments']
    assert schema['properties']['assessments']['minItems'] == len(draft['assessments'])
    assert 'statement' not in schema['$defs']['AssessmentSelection']['properties']


@pytest.mark.parametrize('retained_indices', [[], [0], [0, 1]])
def test_prompt_change_preserves_all_replaced_mixed_and_retained_restoration(retained_indices):
    question, hits, draft, rows, context = fixture()
    payload = rag._generation_payload(question, hits, {'model': 'controlled'}, draft=draft, context_data=context)
    data = json.loads(payload['messages'][1]['content'])
    policy = data['server_review_delta']
    assert policy['version'] == 'scoped-review-delta-v3'
    assert policy['retainable_assessment_ids'] == ['a1', 'a2']
    assert 'required_replacement_assessment_ids' not in policy
    schema = payload['response_format']['json_schema']['schema']
    assert schema['required'] == ['retained_assessment_ids', 'assessments']
    assert schema['properties']['assessments']['minItems'] == 0
    assert schema['properties']['assessments']['maxItems'] == 2
    response = dict(retained_assessment_ids=[f'a{i + 1}' for i in retained_indices],
        assessments=[dict(assessment_id=f'a{i + 1}', **{k: v for k, v in row.items() if k != 'statement'})
                     for i, row in enumerate(rows) if i not in retained_indices])
    assert restore_review_delta(response, draft, hits, policy) == draft


@pytest.mark.parametrize('response', [
    {'retained_assessment_ids': [], 'assessments': []},
    {'retained_assessment_ids': ['a1', 'a1'], 'assessments': []},
    {'retained_assessment_ids': ['a1', 'a99'], 'assessments': []},
])
def test_clearer_prompt_does_not_authorize_implicit_or_invalid_retention(response):
    question, hits, draft, _, context = fixture()
    payload = rag._generation_payload(question, hits, {'model': 'controlled'}, draft=draft, context_data=context)
    policy = json.loads(payload['messages'][1]['content'])['server_review_delta']
    with pytest.raises(ValueError):
        restore_review_delta(response, draft, hits, policy)
