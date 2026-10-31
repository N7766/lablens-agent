"""Lossless fixed-row wire protocol checks; controlled provider, no model quality claims."""
import copy
import json

import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections


def fixture():
    text = 'The retained receipt records final_status=SUCCEEDED. Retry count is not recorded.'
    hits = [dict(chunk_id='receipt', document_id='receipt-document', title='Retained receipt',
                 source='synthetic:receipt', text=text, start=0, end=len(text), content_sha256='test')]
    selected = {'assessments': [
        dict(statement='The recorded final status is SUCCEEDED.', verdict='insufficient',
             explanation='Pending review.', basis='documented_fact', evidence_ids=[]),
        dict(statement='A retry occurred.', verdict='insufficient',
             explanation='Pending review.', basis='documented_fact', evidence_ids=[]),
    ]}
    raw = resolve_selections(selected, hits)
    context = {'scoped_review_contract': make_review_contract('What is known?', raw)}
    response = {'retained_assessment_ids': [], 'assessments': [
        dict(assessment_id='a2', verdict='insufficient', explanation='A linked attempt record is required.',
             basis='historical_observation', evidence_ids=[]),
        dict(assessment_id='a1', verdict='supported', explanation='The receipt records final_status=SUCCEEDED.',
             basis='historical_observation', evidence_ids=list(evidence_catalog(hits))),
    ]}
    return hits, raw, context, response


def provider(monkeypatch, response):
    sent = []
    def post(config, path, payload, usage, kind):
        sent.append(copy.deepcopy(payload))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(response)}}]}
    monkeypatch.setattr(rag, '_post', post)
    return sent


def test_schema_requires_short_ids_and_maps_every_unchanged_statement_once():
    hits, raw, context, _ = fixture()
    original = copy.deepcopy((hits, raw, context))
    payload = rag._generation_payload('What is known?', hits, {'model': 'controlled'}, draft=raw, context_data=context)
    schema = payload['response_format']['json_schema']['schema']
    row = schema['$defs']['AssessmentSelection']
    assert row['properties']['assessment_id'] == {'type': 'string', 'enum': ['a1', 'a2']}
    assert 'statement' not in row['properties']
    assert row['additionalProperties'] is False
    assert set(row['required']) == {'assessment_id', 'verdict', 'explanation', 'basis', 'evidence_ids'}
    assert schema['properties']['assessments']['minItems'] == 0
    assert schema['properties']['assessments']['maxItems'] == 2
    data = json.loads(payload['messages'][1]['content'])
    assert [r['assessment_id'] for r in data['untrusted_draft']['assessments']] == ['a1', 'a2']
    assert [r['statement'] for r in data['untrusted_draft']['assessments']] == context['scoped_review_contract']['statements']
    assert 'statements' not in data['server_review_contract']
    assert data['passages'] == evidence_catalog(hits)
    assert payload['max_tokens'] == 2600
    assert (hits, raw, context) == original
    assert 'Explicit user-requested slots' in payload['messages'][0]['content']
    assert 'an earlier assistant answer is not evidence' in payload['messages'][0]['content']


def test_one_review_call_restores_order_preserves_statement_and_corrects_other_fields(monkeypatch):
    hits, raw, context, response = fixture()
    before = copy.deepcopy((raw, response))
    calls = provider(monkeypatch, response)
    result = rag.review_assessments('What is known?', raw, hits, {}, {'model': 'controlled'}, context_data=context)
    assert len(calls) == 1
    assert result['status'] == 'answered'
    assert [r['statement'] for r in result['assessments']] == context['scoped_review_contract']['statements']
    assert result['assessments'][0]['verdict'] == 'supported'
    assert result['assessments'][0]['basis'] == 'historical_observation'
    assert result['assessments'][0]['explanation'] == response['assessments'][1]['explanation']
    assert result['assessments'][0]['quotes'][0]['quote'] == hits[0]['text']
    assert result['assessments'][1]['verdict'] == 'insufficient'
    assert result['generation_review']['calls'] == 1
    assert result['generation_review']['payload_format'] == 'scoped-review-delta-v3'
    assert result['assessment_validation']['selected_evidence_checks']['findings'] == []
    assert (raw, response) == before


@pytest.mark.parametrize('failure', [
    'missing', 'duplicate', 'unknown', 'bool-index', 'integer-index', 'float-index', 'numeric-string',
    'fabricated-statement', 'replaced-id-with-statement', 'extra-row', 'missing-field', 'extra-top-level',
    'wrong-rows-type', 'empty', 'unknown-evidence', 'duplicate-evidence', 'invalid-verdict', 'invalid-basis',
])
def test_malformed_compact_response_is_withheld_without_a_second_call(monkeypatch, failure):
    hits, raw, context, response = fixture()
    rows = response['assessments']
    if failure == 'missing': rows.pop()
    elif failure == 'duplicate': rows[0]['assessment_id'] = rows[1]['assessment_id']
    elif failure == 'unknown': rows[0]['assessment_id'] = 'a3'
    elif failure == 'bool-index': rows[0]['assessment_id'] = True
    elif failure == 'integer-index': rows[0]['assessment_id'] = 1
    elif failure == 'float-index': rows[0]['assessment_id'] = 1.0
    elif failure == 'numeric-string': rows[0]['assessment_id'] = '1'
    elif failure == 'fabricated-statement': rows[0]['statement'] = 'A fabricated proposition.'
    elif failure == 'replaced-id-with-statement':
        rows[0].pop('assessment_id'); rows[0]['statement'] = raw['assessments'][1]['statement']
    elif failure == 'extra-row': rows.append(copy.deepcopy(rows[0]))
    elif failure == 'missing-field': rows[0].pop('basis')
    elif failure == 'extra-top-level': response['notes'] = 'Unverified additional facts.'
    elif failure == 'wrong-rows-type': response['assessments'] = {'a1': rows[0]}
    elif failure == 'empty': response['assessments'] = []
    elif failure == 'unknown-evidence': rows[1]['evidence_ids'] = ['invented']
    elif failure == 'duplicate-evidence': rows[1]['evidence_ids'] *= 2
    elif failure == 'invalid-verdict': rows[1]['verdict'] = 'certain'
    elif failure == 'invalid-basis': rows[1]['basis'] = 'guessed_fact'
    calls = provider(monkeypatch, response)
    with pytest.raises(rag.RAGError, match='answer withheld'):
        rag.review_assessments('What is known?', raw, hits, {}, {'model': 'controlled'}, context_data=context)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['reordered', 'different', 'duplicate', 'missing', 'no-draft', 'no-contract-statements'])
def test_contract_cannot_rebind_ids_before_provider_request(monkeypatch, failure):
    hits, raw, context, response = fixture()
    statements = context['scoped_review_contract']['statements']
    if failure == 'reordered': statements.reverse()
    elif failure == 'different': statements[0] = 'A different requested fact.'
    elif failure == 'duplicate': statements[1] = statements[0]
    elif failure == 'missing': statements.pop()
    elif failure == 'no-draft': raw = None
    elif failure == 'no-contract-statements': context['scoped_review_contract'].pop('statements')
    calls = provider(monkeypatch, response)
    with pytest.raises(ValueError, match='Scoped review'):
        rag.review_assessments('What is known?', raw, hits, {}, {'model': 'controlled'}, context_data=context)
    assert calls == []


def test_legacy_rag_without_contract_keeps_statement_protocol(monkeypatch):
    hits, raw, _, response = fixture()
    response = rag._restore_review_statements({'assessments': response['assessments']}, [r['statement'] for r in raw['assessments']])
    calls = provider(monkeypatch, response)
    result = rag.review_assessments('What is known?', raw, hits, {}, {'model': 'controlled'})
    assert len(calls) == 1
    schema = calls[0]['response_format']['json_schema']['schema']['$defs']['AssessmentSelection']
    assert 'statement' in schema['properties'] and 'assessment_id' not in schema['properties']
    assert result['generation_review']['payload_format'] == 'full-review-v1'
    assert result['status'] == 'answered'


def test_fixed_id_does_not_bypass_existing_numeric_checks(monkeypatch):
    hits, raw, context, response = fixture()
    response['assessments'][1]['explanation'] = 'The receipt records final_status=SUCCEEDED in 18 ms.'
    calls = provider(monkeypatch, response)
    result = rag.review_assessments('What is known?', raw, hits, {}, {'model': 'controlled'}, context_data=context)
    assert len(calls) == 1
    checks = result['assessment_validation']['selected_evidence_checks']
    assert checks['findings'][0]['rule'] == 'quantity_not_anchored_in_selected_quotes'
    assert checks['findings'][0]['quantities'] == ['18 ms']
    assert result['assessments'][0]['verdict'] == 'insufficient'


def test_non_catalog_draft_keeps_its_original_quote_while_ids_bind_the_same_statement():
    hits, raw, context, _ = fixture()
    raw['assessments'][0].update(citation_ids=['receipt'], quotes=[{'chunk_id': 'receipt', 'quote': 'final_status=SUCCEEDED'}])
    payload = rag._generation_payload('What is known?', hits, {'model': 'controlled'}, draft=raw, context_data=context)
    draft = json.loads(payload['messages'][1]['content'])['untrusted_draft']
    assert draft['assessments'][0]['assessment_id'] == 'a1'
    assert {k: v for k, v in draft['assessments'][0].items() if k != 'assessment_id'} == raw['assessments'][0]
