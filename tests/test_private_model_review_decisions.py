"""Independent v4 restoration and production-route controls, with no model calls.

The real v3 failure remains a failure. Controlled responses establish protocol
behavior only; structural schema inspection below is not JSON Schema validation.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import Mock

import httpx
import pytest

from lablens import answer_quality, rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections
from lablens.review_decisions import decision_schema, restore_review_decisions, review_policy
from lablens.review_delta import restore_review_delta, review_policy as legacy_policy


V3 = 'scoped-review-delta-v3'
V4 = 'scoped-review-decisions-v4'
ROOT = Path(__file__).resolve().parents[1]
USAGE = dict(prompt_tokens=13, completion_tokens=7, total_tokens=20)


def case(count=3, *, text=None, source='synthetic/observations.md'):
    text = text or 'The recorded status is complete.\nThe operator label is local.\nThe run label is synthetic.'
    hits = rag.chunk_documents([rag.RAGDocument(
        id='review-record', title='Synthetic review observations', source=source, text=text)])
    passages = list(evidence_catalog(hits))
    rows = [dict(statement=f'Recorded observation {i + 1}.', verdict='supported',
                 explanation='The selected source records this observation.', basis='documented_fact',
                 evidence_ids=[passages[min(i, len(passages) - 1)]]) for i in range(count)]
    draft = resolve_selections({'assessments': rows}, hits)
    question = 'Check each recorded observation against its selected source.'
    contract = make_review_contract(question, draft)
    return SimpleNamespace(question=question, hits=hits, rows=rows, draft=draft,
                           contract=contract, context={'scoped_review_contract': contract})


def decisions(data, retained=()):
    # Deliberately reverse response order; public report order belongs to the draft.
    return {'decisions': {
        f'a{i + 1}': ({'action': 'retain'} if i in retained else dict(
            action='replace', **{key: value for key, value in data.rows[i].items() if key != 'statement'}))
        for i in reversed(range(len(data.rows)))}}


def route(monkeypatch, data, value, *, protocol=V4, finish='stop', failure=None,
          before_return=None, reported_usage=USAGE):
    sent, observed = [], []
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def post(url, **kwargs):
        sent.append((url, kwargs))
        assert len(sent) == 1, 'Review must not retry or fall back to another protocol'
        if failure is not None:
            raise failure
        if before_return is not None:
            before_return()
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'choices': [{'finish_reason': finish, 'message': {'role': 'assistant', 'content': content}}],
            'usage': reported_usage})

    monkeypatch.setattr(rag, 'bounded_post', post)
    config = dict(base='https://example.invalid/v1', model='synthetic-protocol-fixture',
                  key='unused-test-key', timeout=40, deadline=time.monotonic() + 80)
    if protocol is not None:
        config['review_protocol'] = protocol
    usage = rag._new_usage()

    def run():
        return rag.review_assessments(data.question, data.draft, data.hits, usage, config,
                                      context_data=data.context, observer=observed.append)

    return SimpleNamespace(run=run, sent=sent, observed=observed, usage=usage,
                           content=content, config=config)


@pytest.mark.parametrize('retained', [(), (0, 2), (0, 1, 2)], ids=['all-replace', 'mixed', 'all-retain'])
def test_complete_decisions_restore_in_draft_order_and_detach_every_result(retained):
    data = case()
    policy = review_policy(data.draft, data.hits, data.contract)
    response = decisions(data, retained)
    assert policy['retainable_assessment_ids'] == ['a1', 'a2', 'a3']
    inputs = (data.draft, data.hits, data.contract, policy, response)
    before = copy.deepcopy(inputs)
    restored = restore_review_decisions(response, data.draft, data.hits, policy, data.contract)
    assert restored == data.draft
    assert inputs == before
    for row in restored['assessments']:
        row['quotes'][0]['quote'] = 'changed returned quote'
        row['citation_ids'].append('changed returned citation')
        row['explanation'] = 'changed returned explanation'
    assert inputs == before


def test_mixed_decisions_apply_only_the_selected_replacement_to_the_original_statement():
    data = case()
    policy = review_policy(data.draft, data.hits, data.contract)
    response = decisions(data, retained=(0, 2))
    response['decisions']['a2'].update(verdict='insufficient',
        explanation='The selected source does not establish the requested observation.', evidence_ids=[])
    before = copy.deepcopy((data.draft, response))
    restored = restore_review_decisions(response, data.draft, data.hits, policy, data.contract)
    assert restored['assessments'][0] == data.draft['assessments'][0]
    assert restored['assessments'][2] == data.draft['assessments'][2]
    changed = restored['assessments'][1]
    assert changed['statement'] == data.draft['assessments'][1]['statement']
    assert changed['verdict'] == 'insufficient' and changed['quotes'] == changed['citation_ids'] == []
    assert (data.draft, response) == before


def test_server_placeholder_requires_explicit_replacement_even_if_insufficient():
    data = case(1)
    data.draft['assessments'][0].update(verdict='insufficient', quotes=[], citation_ids=[],
                                      explanation='Server placeholder; this question has not been assessed.')
    data.contract = make_review_contract(data.question, data.draft)
    policy = review_policy(data.draft, data.hits, data.contract)
    assert policy['retainable_assessment_ids'] == []
    with pytest.raises(ValueError, match='non-retainable'):
        restore_review_decisions({'decisions': {'a1': {'action': 'retain'}}},
                                 data.draft, data.hits, policy, data.contract)
    replaced = restore_review_decisions(decisions(data), data.draft, data.hits, policy, data.contract)
    assert replaced['assessments'][0]['verdict'] == 'supported'
    assert replaced['assessments'][0]['quotes']


@pytest.mark.parametrize('field,value', [
    ('version', V3), ('implementation_sha256', '0' * 64), ('input_sha256', '0' * 64),
    ('assessment_ids', ['a3', 'a2', 'a1']), ('retainable_assessment_ids', []),
    ('semantic_correctness_verified', True),
])
def test_modified_or_older_policy_is_not_a_retention_authority(field, value):
    data = case()
    policy = review_policy(data.draft, data.hits, data.contract)
    policy[field] = value
    with pytest.raises(ValueError, match='policy does not match'):
        restore_review_decisions(decisions(data, (0, 1, 2)), data.draft, data.hits, policy, data.contract)


@pytest.mark.parametrize('changed', ['draft', 'evidence-text', 'evidence-metadata', 'contract'])
def test_policy_from_another_input_snapshot_is_rejected_without_mutation(changed):
    data = case()
    policy = review_policy(data.draft, data.hits, data.contract)
    response = decisions(data, (0, 1, 2))
    if changed == 'draft':
        data.draft['assessments'][0]['explanation'] = 'A revised, still plausible draft observation.'
    elif changed == 'evidence-text':
        data.hits[0]['text'] += '\nA later observation was added.'
    elif changed == 'evidence-metadata':
        data.hits[0]['source'] = 'synthetic/different-version.md'
    else:
        data.contract['rules'].append('An additional frozen review obligation.')
    inputs = (data.draft, data.hits, data.contract, policy, response)
    before = copy.deepcopy(inputs)
    with pytest.raises(ValueError, match='policy does not match'):
        restore_review_decisions(response, data.draft, data.hits, policy, data.contract)
    assert inputs == before


@pytest.mark.parametrize('changed', ['contract-order', 'different-statement', 'duplicate-statement', 'blank'])
def test_policy_requires_the_exact_unique_nonblank_immutable_statement_contract(changed):
    data = case()
    if changed == 'contract-order':
        data.contract['statements'].reverse()
    elif changed == 'different-statement':
        data.contract['statements'][0] = 'Another proposition.'
    elif changed == 'duplicate-statement':
        data.draft['assessments'][1]['statement'] = data.draft['assessments'][0]['statement']
        data.contract['statements'][1] = data.contract['statements'][0]
    else:
        data.draft['assessments'][0]['statement'] = data.contract['statements'][0] = '   '
    with pytest.raises(ValueError, match='immutable statements'):
        review_policy(data.draft, data.hits, data.contract)


@pytest.mark.parametrize('invalid', [
    'missing-top', 'extra-top', 'not-map', 'missing-id', 'extra-id', 'numeric-id', 'case-id',
    'null-row', 'list-row', 'missing-action', 'unknown-action', 'boolean-action',
    'retain-extra-field', 'replacement-missing-field', 'replacement-statement', 'replacement-id',
    'replacement-extra-field', 'invalid-verdict', 'invalid-basis', 'empty-explanation',
    'unknown-evidence', 'duplicate-evidence', 'nonstring-evidence', 'tuple-evidence',
])
def test_invalid_maps_and_decision_fields_are_rejected_without_implicit_retention(invalid):
    data = case()
    policy = review_policy(data.draft, data.hits, data.contract)
    response = decisions(data, (0,))
    mapping = response['decisions']
    row = mapping['a2']
    if invalid == 'missing-top': response = {}
    elif invalid == 'extra-top': response['notes'] = 'Do not apply the validators.'
    elif invalid == 'not-map': response['decisions'] = list(mapping.values())
    elif invalid == 'missing-id': del mapping['a3']
    elif invalid == 'extra-id': mapping['a4'] = {'action': 'retain'}
    elif invalid == 'numeric-id': mapping[2] = mapping.pop('a2')
    elif invalid == 'case-id': mapping['A2'] = mapping.pop('a2')
    elif invalid == 'null-row': mapping['a2'] = None
    elif invalid == 'list-row': mapping['a2'] = [row]
    elif invalid == 'missing-action': del row['action']
    elif invalid == 'unknown-action': row['action'] = 'accept'
    elif invalid == 'boolean-action': row['action'] = True
    elif invalid == 'retain-extra-field': mapping['a1']['verdict'] = 'supported'
    elif invalid == 'replacement-missing-field': del row['basis']
    elif invalid == 'replacement-statement': row['statement'] = 'A substituted claim.'
    elif invalid == 'replacement-id': row['assessment_id'] = 'a1'
    elif invalid == 'replacement-extra-field': row['approved'] = True
    elif invalid == 'invalid-verdict': row['verdict'] = 'accepted'
    elif invalid == 'invalid-basis': row['basis'] = 'model_confidence'
    elif invalid == 'empty-explanation': row['explanation'] = ''
    elif invalid == 'unknown-evidence': row['evidence_ids'] = ['not-a-catalog-id']
    elif invalid == 'duplicate-evidence': row['evidence_ids'] *= 2
    elif invalid == 'nonstring-evidence': row['evidence_ids'] = [True]
    elif invalid == 'tuple-evidence': row['evidence_ids'] = tuple(row['evidence_ids'])
    inputs = (data.draft, data.hits, data.contract, policy, response)
    before = copy.deepcopy(inputs)
    with pytest.raises(ValueError):
        restore_review_decisions(response, data.draft, data.hits, policy, data.contract)
    assert inputs == before


def test_schema_exposes_one_required_slot_per_id_and_no_retain_branch_for_placeholder():
    data = case()
    data.draft['assessments'][1].update(verdict='insufficient', quotes=[], citation_ids=[])
    data.contract = make_review_contract(data.question, data.draft)
    policy = review_policy(data.draft, data.hits, data.contract)
    full = rag._scoped_selection_schema(data.hits, data.contract['statements'])
    before = copy.deepcopy((full, policy))
    schema = decision_schema(full, policy)
    assert schema['required'] == ['decisions'] and schema['additionalProperties'] is False
    mapping = schema['properties']['decisions']
    assert mapping['required'] == ['a1', 'a2', 'a3']
    assert set(mapping['properties']) == {'a1', 'a2', 'a3'}
    assert mapping['additionalProperties'] is False
    for identity in ('a1', 'a3'):
        retain, replacement = mapping['properties'][identity]['anyOf']
        assert retain == dict(type='object', properties={'action': {'type': 'string', 'enum': ['retain']}},
                              required=['action'], additionalProperties=False)
        assert replacement['properties']['action']['enum'] == ['replace']
    placeholder = mapping['properties']['a2']
    assert 'anyOf' not in placeholder and placeholder['properties']['action']['enum'] == ['replace']
    assert set(placeholder['required']) == {'action', 'verdict', 'explanation', 'basis', 'evidence_ids'}
    assert 'assessment_id' not in placeholder['properties'] and 'statement' not in placeholder['properties']
    assert placeholder['additionalProperties'] is False
    assert placeholder['properties']['evidence_ids']['items']['enum'] == list(evidence_catalog(data.hits))
    assert (full, policy) == before
    placeholder['properties']['evidence_ids']['items']['enum'].append('not-real')
    assert (full, policy) == before
    assert 'not-real' not in mapping['properties']['a1']['anyOf'][1]['properties']['evidence_ids']['items']['enum']


@pytest.mark.parametrize('content', [
    '{"decisions":{},"decisions":{}}',
    r'{"decisions":{},"\u0064ecisions":{}}',
    '{"decisions":{"a1":{"action":"retain"},"a1":{"action":"retain"}}}',
    r'{"decisions":{"a1":{"action":"retain"},"\u00611":{"action":"retain"}}}',
    r'{"decisions":{"a1":{"action":"retain","\u0061ction":"replace"}}}',
    '{"extra":[{"nested":1,"nested":2}]}',
    '{"decisions":{"a1":{"action":"replace","explanation":NaN}}}',
    '{"decisions":{"a1":{"action":"replace","explanation":Infinity}}}',
    '{"decisions":{"a1":{"action":"replace","explanation":-Infinity}}}',
])
def test_raw_json_rejects_duplicate_decoded_names_nested_duplicates_and_nonfinite_values(content):
    observed = []
    with pytest.raises(rag.RAGError, match='invalid JSON') as caught:
        rag._generation_json({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]},
                             observer=observed.append, phase='review')
    assert caught.value.status == 'invalid_citations'
    assert len(observed) == 1 and observed[0]['content'] == content
    assert observed[0]['phase'] == 'review'


def test_duplicate_detection_does_not_reject_nonduplicate_escaped_names_or_literal_nan_text():
    parsed = rag._strict_generation_json(r'{"decisions":{"\u00611":{"action":"retain"}},"note":"NaN"}')
    assert parsed == {'decisions': {'a1': {'action': 'retain'}}, 'note': 'NaN'}


@pytest.mark.parametrize('retained', [(), (0, 2), (0, 1, 2)], ids=['all-replace', 'mixed', 'all-retain'])
def test_production_route_uses_v4_once_preserves_usage_and_audits_raw_decisions(monkeypatch, retained):
    data = case()
    response = decisions(data, retained)
    before = copy.deepcopy((data.draft, data.hits, data.context, response))
    transport = route(monkeypatch, data, response)
    transport.usage.update(model_calls=2, generation_tokens=dict(prompt_tokens=11, completion_tokens=5, total_tokens=16))
    result = transport.run()
    assert len(transport.sent) == result['generation_review']['calls'] == 1
    assert transport.usage['model_calls'] == 3
    assert transport.usage['generation_tokens'] == dict(prompt_tokens=24, completion_tokens=12, total_tokens=36)
    assert transport.usage['provider_usage_unknown'] is False
    url, kwargs = transport.sent[0]
    assert url == 'https://example.invalid/v1/chat/completions'
    assert kwargs['follow_redirects'] is False and 0 < kwargs['timeout'] <= 40
    payload = kwargs['json']
    assert payload['max_tokens'] == 2600
    assert payload['response_format']['json_schema']['schema']['required'] == ['decisions']
    sent_data = json.loads(payload['messages'][1]['content'])
    assert 'server_review_delta' not in sent_data
    assert sent_data['server_review_decisions']['version'] == V4
    assert result['assessments'] == data.draft['assessments']
    review = result['generation_review']
    assert review['payload_format'] == V4 and 'delta' not in review
    audit = review['decisions']
    assert set(audit['retained_assessment_ids']) == {f'a{i + 1}' for i in retained}
    assert set(audit['replaced_assessment_ids']) == {f'a{i + 1}' for i in range(3) if i not in retained}
    assert audit['reviewed_response'] == response and audit['semantic_correctness_verified'] is False
    assert audit['input_sha256'] == sent_data['server_review_decisions']['input_sha256']
    assert review['reviewed_sha256'] == hashlib.sha256(
        json.dumps(response, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert len(transport.observed) == 1 and transport.observed[0]['content'] == transport.content
    assert result['assessment_validation']['semantic_entailment_verified'] is False
    assert result['assessment_validation']['selected_evidence_checks']['semantic_entailment_verified'] is False
    audit['reviewed_response']['decisions'].clear()
    result['assessments'][0]['quotes'][0]['quote'] = 'changed output'
    assert (data.draft, data.hits, data.context, response) == before


@pytest.mark.parametrize('failure', ['missing-id', 'legacy-output', 'duplicate-json', 'invalid-json', 'length', 'timeout'])
def test_failed_production_review_keeps_one_attempt_usage_and_available_raw_content(monkeypatch, failure):
    data = case()
    value = decisions(data, (0, 1, 2))
    if failure == 'missing-id': del value['decisions']['a2']
    elif failure == 'legacy-output': value = {'retained_assessment_ids': ['a1', 'a2', 'a3'], 'assessments': []}
    elif failure == 'duplicate-json': value = '{"decisions":{},"decisions":{}}'
    elif failure == 'invalid-json': value = '{invalid'
    transport = route(monkeypatch, data, value, finish='length' if failure == 'length' else 'stop',
                      failure=httpx.ReadTimeout('synthetic timeout') if failure == 'timeout' else None)
    before = copy.deepcopy(data.draft)
    with pytest.raises(rag.RAGError) as caught:
        transport.run()
    assert len(transport.sent) == transport.usage['model_calls'] == 1
    assert data.draft == before
    if failure == 'timeout':
        assert transport.usage['provider_usage_unknown'] is True
        assert all(value is None for value in transport.usage['generation_tokens'].values())
        assert transport.observed == []
    else:
        assert transport.usage['generation_tokens'] == USAGE
        assert transport.usage['provider_usage_unknown'] is False
        assert transport.observed[0]['content'] == transport.content
        assert caught.value.status == 'invalid_citations'
    if failure in ('missing-id', 'legacy-output'):
        assert caught.value.review_diagnostic['stage'] == 'restore_or_validate'


def test_missing_response_usage_is_not_fabricated_when_protocol_validation_succeeds(monkeypatch):
    data = case()
    transport = route(monkeypatch, data, decisions(data, (0, 1, 2)), reported_usage=None)
    result = transport.run()
    assert result['generation_review']['payload_format'] == V4
    assert transport.usage['model_calls'] == 1 and transport.usage['provider_usage_unknown'] is True
    assert all(value is None for value in transport.usage['generation_tokens'].values())


def test_draft_drift_during_provider_request_rejects_the_old_policy_and_preserves_usage(monkeypatch):
    data = case()
    transport = route(monkeypatch, data, decisions(data, (0, 1, 2)), before_return=lambda:
        data.draft['assessments'][0].update(explanation='The draft was changed while the request was in flight.'))
    with pytest.raises(rag.RAGError) as caught:
        transport.run()
    assert 'policy does not match' in caught.value.review_diagnostic['message']
    assert len(transport.sent) == transport.usage['model_calls'] == 1
    assert transport.usage['generation_tokens'] == USAGE
    assert transport.observed[0]['content'] == transport.content


@pytest.mark.parametrize('guard', ['causal', 'configuration-history'])
def test_all_retained_rows_still_run_both_production_guards(monkeypatch, guard):
    data = case(1, text='The option is enabled.',
                source='synthetic/configuration.yaml' if guard == 'configuration-history' else 'synthetic/report.md')
    data.draft['assessments'][0].update(
        statement='The runtime executed the enabled option.' if guard == 'configuration-history' else 'The option caused the improvement.',
        explanation='The option is enabled.',
        basis='historical_observation' if guard == 'configuration-history' else 'documented_fact')
    data.contract = make_review_contract(data.question, data.draft)
    data.context = {'scoped_review_contract': data.contract}
    assert review_policy(data.draft, data.hits, data.contract)['retainable_assessment_ids'] == ['a1']
    quality_spy = Mock(wraps=answer_quality.enforce_review_checks)
    final_spy = Mock(wraps=rag.validate_assessments)
    monkeypatch.setattr(answer_quality, 'enforce_review_checks', quality_spy)
    monkeypatch.setattr(rag, 'validate_assessments', final_spy)
    transport = route(monkeypatch, data, {'decisions': {'a1': {'action': 'retain'}}})
    result = transport.run()
    assert quality_spy.call_count == final_spy.call_count == 1
    assert quality_spy.call_args.args[1:3] == (data.draft, data.draft)
    assert quality_spy.call_args.kwargs['hits'] == data.hits
    assert len(transport.sent) == 1 and result['assessments'][0]['verdict'] == 'insufficient'
    rules = {item['rule'] for item in result['assessment_validation']['adjustments']}
    expected = ('config_code_cannot_establish_historical_or_causal_fact'
                if guard == 'configuration-history' else 'causal_claim_requires_external_controlled_review')
    assert expected in rules
    assert result['generation_review']['decisions']['reviewed_response'] == {'decisions': {'a1': {'action': 'retain'}}}
    assert data.draft['assessments'][0]['verdict'] == 'supported'


def test_final_guard_adjustments_do_not_rewrite_raw_replacement_audit(monkeypatch):
    data = case(1, text='An improvement was observed.')
    response = decisions(data)
    response['decisions']['a1'].update(basis='causal_claim', explanation='The option caused the improvement.')
    transport = route(monkeypatch, data, response)
    result = transport.run()
    assert result['assessments'][0]['verdict'] == 'insufficient'
    audit = result['generation_review']['decisions']['reviewed_response']
    assert audit == response and audit['decisions']['a1']['verdict'] == 'supported'
    assert result['assessments'][0]['explanation'] != audit['decisions']['a1']['explanation']


@pytest.mark.parametrize('protocol', [None, V3, V4])
def test_provider_configuration_defaults_to_v3_and_enables_v4_only_explicitly(monkeypatch, protocol):
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    for key, value in dict(LLM_BASE_URL='http://127.0.0.1:18080/v1', LLM_MODEL='synthetic',
                          LLM_API_KEY='synthetic-test-key', RAG_TIMEOUT_SECONDS='40',
                          RAG_TOTAL_SECONDS='80', EMBEDDING_BATCH_SIZE='1').items():
        monkeypatch.setenv(key, value)
    if protocol is None: monkeypatch.delenv('LABLENS_REVIEW_PROTOCOL', raising=False)
    else: monkeypatch.setenv('LABLENS_REVIEW_PROTOCOL', protocol)
    forbidden_http = Mock(side_effect=AssertionError('Configuration must not send HTTP'))
    monkeypatch.setattr(rag, 'bounded_post', forbidden_http)
    config = rag.provider_config('generation')
    assert config['review_protocol'] == (protocol or V3)
    data = case()
    payload = rag._generation_payload(data.question, data.hits, config, draft=data.draft, context_data=data.context)
    request = json.loads(payload['messages'][1]['content'])
    assert ('server_review_decisions' in request) == (protocol == V4)
    assert ('server_review_delta' in request) == (protocol != V4)
    forbidden_http.assert_not_called()


@pytest.mark.parametrize('protocol', ['', 'scoped-review-decisions-v5', 'SCOPED-REVIEW-DECISIONS-V4'])
def test_unknown_protocol_fails_before_http_in_both_config_and_review_route(monkeypatch, protocol):
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.setenv('LABLENS_REVIEW_PROTOCOL', protocol)
    data = case()
    transport = route(monkeypatch, data, decisions(data), protocol=protocol)
    with pytest.raises(rag.RAGError, match='Unknown review protocol'):
        rag.provider_config('generation')
    with pytest.raises(rag.RAGError, match='Unknown review protocol'):
        transport.run()
    assert transport.sent == [] and transport.usage['model_calls'] == 0


def test_default_production_route_still_uses_v3_without_an_implicit_upgrade(monkeypatch):
    data = case()
    value = {'retained_assessment_ids': ['a1', 'a2', 'a3'], 'assessments': []}
    transport = route(monkeypatch, data, value, protocol=None)
    result = transport.run()
    assert result['generation_review']['payload_format'] == V3
    assert result['assessments'] == data.draft['assessments']
    request = json.loads(transport.sent[0][1]['json']['messages'][1]['content'])
    assert 'server_review_delta' in request and 'server_review_decisions' not in request
    assert len(transport.sent) == 1


def test_actual_v3_failure_is_still_rejected_and_not_regraded_as_a_v4_pass(monkeypatch):
    # Read the immutable, public synthetic response; never query a provider.
    body_path = ROOT / 'docs/evaluations/conversation-round19/runtime/probe-03/run/03-scoped_review/response.sanitized.txt'
    body = json.loads(body_path.read_text())
    content = body['choices'][0]['message']['content']
    assert hashlib.sha256(content.encode()).hexdigest() == 'd6e85e95102e30c2660a7dc55a4565a6815588faa3b492ac829ca45c292530d4'
    hits = rag.chunk_documents([
        rag.RAGDocument(id='synthetic-baseline', title='Synthetic compatibility data',
            source='synthetic/results.md', text='本表仅为部署兼容性测试数据。\nSFT 基线：准确率 86%。'),
        rag.RAGDocument(id='synthetic-config', title='Synthetic configuration',
            source='synthetic/config.yaml', text='cache_enabled: true\n# 没有提供对应历史运行日志。'),
    ])
    baseline = next(key for key, value in evidence_catalog(hits).items() if 'SFT 基线' in value['quote'])
    draft = resolve_selections({'assessments': [
        dict(statement='资料包含 SFT 基线。', verdict='supported', basis='documented_fact',
             explanation='合成资料明确列出了 SFT 基线。', evidence_ids=[baseline]),
        dict(statement='配置足以证明历史运行启用了缓存。', verdict='insufficient', basis='historical_observation',
             explanation='没有对应历史运行日志；配置记录不能证明历史执行。', evidence_ids=[]),
    ]}, hits)
    question = '核对资料中的 SFT 基线，并区分配置记录与历史运行证据。'
    contract = make_review_contract(question, draft)
    policy = legacy_policy(draft, hits, contract)
    with pytest.raises(ValueError, match='overlapping replacement ID'):
        restore_review_delta(json.loads(content), draft, hits, policy)
    data = SimpleNamespace(question=question, draft=draft, hits=hits,
                           context={'scoped_review_contract': contract})
    transport = route(monkeypatch, data, content, protocol=None, reported_usage=body['usage'])
    with pytest.raises(rag.RAGError) as caught:
        transport.run()
    assert 'overlapping replacement ID' in caught.value.review_diagnostic['message']
    assert len(transport.sent) == transport.usage['model_calls'] == 1
    assert transport.observed[0]['content'] == content
    assert transport.usage['generation_tokens'] == {key: body['usage'][key] for key in USAGE}
