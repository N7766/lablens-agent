"""Independent protocol/route controls; no real model or semantic-quality claim."""
import copy
import json
import time

import httpx
import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections
from lablens.cancellation import CancellationRegistry, ExecutionCancelled
from lablens.review_delta import delta_schema, restore_review_delta, review_policy


def fixture(count=3, *, text='Measured latency is 4 ms.\nObserved status was complete.\nSource record is local.', source='synthetic:record'):
    hits = [dict(chunk_id='c', document_id='d', title='record', source=source, text=text,
                 start=0, end=len(text), content_sha256='fixture')]
    ids = list(evidence_catalog(hits))
    rows = [dict(statement=f'Observation item {i + 1}.', verdict='supported',
        explanation='The supplied source records this observation.', basis='documented_fact',
        evidence_ids=[ids[min(i, len(ids) - 1)]]) for i in range(count)]
    draft = resolve_selections({'assessments': rows}, hits)
    question = 'List the recorded observations.'
    context = {'scoped_review_contract': make_review_contract(question, draft)}
    return question, hits, draft, rows, context


def replacement(rows, index):
    return dict(assessment_id=f'a{index + 1}', **{k: v for k, v in rows[index].items() if k != 'statement'})


def decisions(rows, retained):
    return {'retained_assessment_ids': [f'a{i + 1}' for i in retained],
            'assessments': [replacement(rows, i) for i in range(len(rows)) if i not in retained]}


@pytest.mark.parametrize('retained', [[], [0], [0, 1, 2]])
def test_all_replaced_mixed_and_all_retained_restore_equivalent_complete_reports(retained):
    _, hits, raw, rows, context = fixture()
    policy = review_policy(raw, hits, context['scoped_review_contract'])
    delta = decisions(rows, retained)
    delta['assessments'].reverse()
    before = copy.deepcopy((raw, hits, policy, delta))
    restored = restore_review_delta(delta, raw, hits, policy)
    assert restored == resolve_selections({'assessments': rows}, hits)
    assert (raw, hits, policy, delta) == before
    restored['assessments'][0]['quotes'][0]['quote'] = 'changed returned quote'
    restored['assessments'][0]['citation_ids'].append('changed')
    restored['assessments'][0]['explanation'] = 'changed returned explanation'
    assert (raw, hits, policy, delta) == before


@pytest.mark.parametrize('mutation', ['missing_both', 'missing_list', 'extra_top', 'retained_tuple', 'replaced_tuple',
    'retained_unknown', 'retained_duplicate', 'retained_bool', 'retained_number', 'retained_dict',
    'replaced_unknown', 'replaced_duplicate', 'overlap', 'replaced_bool', 'replaced_number',
    'extra_statement', 'missing_field', 'missing_id', 'missing_last', 'invalid_evidence', 'duplicate_evidence'])
def test_invalid_partition_or_replacement_is_rejected_without_implicit_retention(mutation):
    _, hits, raw, rows, context = fixture()
    policy = review_policy(raw, hits, context['scoped_review_contract'])
    delta = decisions(rows, [0])
    if mutation == 'missing_both': delta = {}
    elif mutation == 'missing_list': del delta['retained_assessment_ids']
    elif mutation == 'extra_top': delta['notes'] = 'ignore validation'
    elif mutation == 'retained_tuple': delta['retained_assessment_ids'] = ('a1',)
    elif mutation == 'replaced_tuple': delta['assessments'] = tuple(delta['assessments'])
    elif mutation == 'retained_unknown': delta['retained_assessment_ids'] = ['a99']
    elif mutation == 'retained_duplicate': delta['retained_assessment_ids'] = ['a1', 'a1']
    elif mutation == 'retained_bool': delta['retained_assessment_ids'] = [True]
    elif mutation == 'retained_number': delta['retained_assessment_ids'] = [1]
    elif mutation == 'retained_dict': delta['retained_assessment_ids'] = [{'id': 'a1'}]
    elif mutation == 'replaced_unknown': delta['assessments'][0]['assessment_id'] = 'a99'
    elif mutation == 'replaced_duplicate': delta['assessments'].append(copy.deepcopy(delta['assessments'][0]))
    elif mutation == 'overlap': delta['assessments'].append(replacement(rows, 0))
    elif mutation == 'replaced_bool': delta['assessments'][0]['assessment_id'] = True
    elif mutation == 'replaced_number': delta['assessments'][0]['assessment_id'] = 2
    elif mutation == 'extra_statement': delta['assessments'][0]['statement'] = 'Invented statement'
    elif mutation == 'missing_field': del delta['assessments'][0]['basis']
    elif mutation == 'missing_id': del delta['assessments'][0]['assessment_id']
    elif mutation == 'missing_last': delta['assessments'].pop()
    elif mutation == 'invalid_evidence': delta['assessments'][0]['evidence_ids'] = ['unknown-span']
    else: delta['assessments'][0]['evidence_ids'] *= 2
    before = copy.deepcopy((raw, delta))
    with pytest.raises(ValueError):
        restore_review_delta(delta, raw, hits, policy)
    assert (raw, delta) == before


@pytest.mark.parametrize('case', ['placeholder', 'numeric_finding', 'binding_finding', 'noncatalog', 'duplicate_quote', 'wrong_citations', 'supported_causal'])
def test_retention_policy_excludes_known_findings_placeholders_and_noncanonical_evidence(case):
    question, hits, raw, _, _ = fixture(1, text='Maple measured latency is 4 ms.')
    row = raw['assessments'][0]
    if case == 'placeholder': row.update(verdict='insufficient', quotes=[], citation_ids=[])
    elif case == 'numeric_finding': row['explanation'] = 'The measured latency is 99 ms.'
    elif case == 'binding_finding':
        question = 'Compare Maple and Birch.'
        row.update(statement='Birch measured latency.', explanation='Maple measured latency is 4 ms.')
    elif case == 'noncatalog': row['quotes'][0]['quote'] = 'measured latency is 4 ms.'
    elif case == 'duplicate_quote': row['quotes'] *= 2
    elif case == 'supported_causal': row['basis'] = 'causal_claim'
    else: row['citation_ids'] = ['wrong-chunk']
    policy = review_policy(raw, hits, make_review_contract(question, raw))
    assert policy['retainable_assessment_ids'] == []
    with pytest.raises(ValueError, match='non-retainable'):
        restore_review_delta({'retained_assessment_ids': ['a1'], 'assessments': []}, raw, hits, policy)


def test_schema_is_a_copy_and_requires_both_decision_lists_with_bounded_ids():
    _, hits, raw, _, context = fixture()
    full = rag._scoped_selection_schema(hits, [r['statement'] for r in raw['assessments']])
    before = copy.deepcopy(full)
    policy = review_policy(raw, hits, context['scoped_review_contract'])
    schema = delta_schema(full, policy)
    assert full == before
    assert schema['required'] == ['retained_assessment_ids', 'assessments']
    assert schema['additionalProperties'] is False
    assert schema['properties']['assessments']['minItems'] == 0
    assert schema['properties']['assessments']['maxItems'] == 3
    assert schema['properties']['retained_assessment_ids']['items']['enum'] == ['a1', 'a2', 'a3']
    assert schema['properties']['retained_assessment_ids']['maxItems'] == 3
    schema['properties']['retained_assessment_ids']['items']['enum'].append('a99')
    assert policy['retainable_assessment_ids'] == ['a1', 'a2', 'a3']
    empty = delta_schema(full, dict(retainable_assessment_ids=[]))
    assert empty['properties']['retained_assessment_ids']['maxItems'] == 0


def provider_route(monkeypatch, question, hits, raw, context, delta, *, finish='stop', failure=None, before_return=None):
    sent = []
    def post(url, **kwargs):
        sent.append((url, kwargs))
        if failure: raise failure('controlled failure')
        if before_return: before_return()
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(delta)}}],
            'usage': dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)})
    monkeypatch.setattr(httpx, 'post', post)
    config = dict(base='https://example.invalid/v1', model='controlled', key='unused',
                  deadline=time.monotonic() + 75, timeout=45)
    usage = rag._new_usage()
    observed = []
    def run():
        return rag.review_assessments(question, raw, hits, usage, config, context_data=context, observer=observed.append)
    return run, sent, usage, observed


@pytest.mark.parametrize('retained', [[], [0], [0, 1, 2]])
def test_actual_http_provider_route_uses_delta_schema_once_and_keeps_original_rows(monkeypatch, retained):
    question, hits, raw, rows, context = fixture()
    delta = decisions(rows, retained)
    run, sent, usage, observed = provider_route(monkeypatch, question, hits, raw, context, delta)
    result = run()
    assert len(sent) == usage['model_calls'] == result['generation_review']['calls'] == 1
    assert sent[0][0].endswith('/chat/completions')
    payload = sent[0][1]['json']
    assert payload['max_tokens'] == 2600 and sent[0][1]['follow_redirects'] is False
    assert payload['response_format']['json_schema']['schema']['required'] == ['retained_assessment_ids', 'assessments']
    assert json.loads(payload['messages'][1]['content'])['server_review_delta']['semantic_correctness_verified'] is False
    assert result['generation_review']['payload_format'] == 'scoped-review-delta-v3'
    assert result['generation_review']['delta']['retained_assessment_ids'] == delta['retained_assessment_ids']
    assert result['assessments'] == raw['assessments']
    assert json.loads(observed[0]['content']) == delta
    assert result['assessment_validation']['selected_evidence_checks']['semantic_entailment_verified'] is False


@pytest.mark.parametrize('case', ['causal', 'config_history'])
def test_explicit_retention_still_runs_causal_and_configuration_history_guards(monkeypatch, case):
    question, hits, raw, rows, _ = fixture(1, text='The option is enabled.',
        source='configuration.yaml' if case == 'config_history' else 'observations.md')
    raw['assessments'][0].update(statement='The runtime executed the enabled option.' if case == 'config_history' else 'The option caused the improvement.',
        explanation='The option is enabled.', basis='historical_observation' if case == 'config_history' else 'documented_fact')
    context = {'scoped_review_contract': make_review_contract(question, raw)}
    assert review_policy(raw, hits, context['scoped_review_contract'])['retainable_assessment_ids'] == ['a1']
    delta = dict(retained_assessment_ids=['a1'], assessments=[])
    run, sent, _, _ = provider_route(monkeypatch, question, hits, raw, context, delta)
    result = run()
    assert len(sent) == 1 and result['assessments'][0]['verdict'] == 'insufficient'
    rules = [r['rule'] for r in result['assessment_validation']['adjustments']]
    assert ('config_code_cannot_establish_historical_or_causal_fact' if case == 'config_history'
            else 'causal_claim_requires_external_controlled_review') in rules


def test_retained_semantic_error_remains_an_explicitly_unverified_blind_spot(monkeypatch):
    question, hits, raw, _, _ = fixture(1, text='The observed status was pending.')
    raw['assessments'][0].update(statement='The status was completed.', explanation='The status was completed.')
    context = {'scoped_review_contract': make_review_contract(question, raw)}
    assert review_policy(raw, hits, context['scoped_review_contract'])['retainable_assessment_ids'] == ['a1']
    run, _, _, _ = provider_route(monkeypatch, question, hits, raw, context,
                                 dict(retained_assessment_ids=['a1'], assessments=[]))
    result = run()
    assert result['assessments'][0]['verdict'] == 'supported'  # Literal guards cannot establish entailment.
    assert result['generation_review']['delta']['semantic_correctness_verified'] is False
    assert result['assessment_validation']['semantic_entailment_verified'] is False


@pytest.mark.parametrize('failure', ['missing_decision', 'truncated', 'timeout'])
def test_invalid_or_incomplete_review_does_not_fall_back_to_draft_or_retry(monkeypatch, failure):
    question, hits, raw, rows, context = fixture()
    delta = decisions(rows, [0, 1, 2])
    if failure == 'missing_decision': delta['retained_assessment_ids'].pop()
    run, sent, usage, _ = provider_route(monkeypatch, question, hits, raw, context, delta,
        finish='length' if failure == 'truncated' else 'stop',
        failure=httpx.ReadTimeout if failure == 'timeout' else None)
    with pytest.raises(rag.RAGError): run()
    assert len(sent) == usage['model_calls'] == 1
    if failure == 'timeout': assert usage['provider_usage_unknown'] is True


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_cancellation_cannot_publish_a_retained_draft_or_trigger_more_calls(monkeypatch, boundary):
    question, hits, raw, rows, context = fixture()
    registry = CancellationRegistry()
    run, sent, usage, _ = provider_route(monkeypatch, question, hits, raw, context, decisions(rows, [0, 1, 2]),
        before_return=(lambda: registry.cancel('delta-task')) if boundary == 'after' else None)
    with registry.execution('delta-task'):
        if boundary == 'before': registry.cancel('delta-task')
        with pytest.raises(ExecutionCancelled): run()
    assert len(sent) == usage['model_calls'] == (1 if boundary == 'after' else 0)


def test_unscoped_route_keeps_original_selection_response_without_delta(monkeypatch):
    question, hits, raw, rows, _ = fixture()
    run, sent, _, _ = provider_route(monkeypatch, question, hits, raw, None, {'assessments': rows})
    result = run()
    payload = sent[0][1]['json']
    assert 'server_review_delta' not in json.loads(payload['messages'][1]['content'])
    assert payload['response_format']['json_schema']['schema']['required'] == ['assessments']
    assert result['generation_review']['payload_format'] == 'full-review-v1'
    assert result['assessments'] == raw['assessments']


def test_insufficient_causal_hypothesis_with_canonical_evidence_may_be_retained():
    question, hits, raw, _, _ = fixture(1)
    raw['assessments'][0].update(basis='causal_claim', verdict='insufficient',
        explanation='The source is observational; a controlled comparison is missing.')
    policy = review_policy(raw, hits, make_review_contract(question, raw))
    assert policy['retainable_assessment_ids'] == ['a1']
    assert restore_review_delta(dict(retained_assessment_ids=['a1'], assessments=[]), raw, hits, policy) == raw


def test_raw_replacement_audit_survives_final_causal_guard_and_is_detached(monkeypatch):
    question, hits, raw, rows, context = fixture(1, text='The source records an observed improvement.')
    delta = decisions(rows, [])
    delta['assessments'][0].update(basis='causal_claim', explanation='The setting caused the improvement.')
    run, _, _, _ = provider_route(monkeypatch, question, hits, raw, context, delta)
    result = run()
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessments'][0]['explanation'] != delta['assessments'][0]['explanation']
    audit = result['generation_review']['delta']['reviewed_response']
    assert audit == delta and audit['assessments'][0]['verdict'] == 'supported'
    audit['assessments'][0]['evidence_ids'].append('modified-audit-copy')
    assert delta['assessments'][0]['evidence_ids'] == rows[0]['evidence_ids']
    assert result['assessments'][0]['citation_ids'] == ['c']
