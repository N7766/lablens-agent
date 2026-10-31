"""Context visibility is a transport property, not a semantic-quality metric."""
import copy
import json

import httpx
import pytest

from lablens.assessment import evidence_catalog, evidence_catalog_metadata, selection_schema
from lablens.conversation import (ConversationBudget, complete_tool_groups,
                                  project_visible_passages)
from lablens.model import HTTPModel, workspace_action_payload
from lablens.rag import chunk_documents, documents_sha256
from lablens.runtime_telemetry import request_body_bytes
from lablens.workspace_agent import WorkspaceAgent, ReadArguments, _compact_tool_output
from test_workspace_agent import request, call, retrieval_server, controlled_review
from test_workspace_actions import configured_model, response


def fixture():
    req = request(documents=[dict(id='a', title='A', source='a.md',
        text='Alpha measured 86 percent.\nThe measured latency was 42 ms.\nAn unexplained gap remains.'),
        dict(id='b', title='B', source='b.md',
        text='Beta measured 82 percent.\nThe measured latency was 61 ms.\nAn unexplained gap remains.')])
    hits = chunk_documents(req.documents)
    observed = {hit['chunk_id']: hit for hit in hits}
    current = dict(question='Compare measured results.', snapshot_id=req.snapshot_id,
        documents_sha256=documents_sha256(req.documents))
    return current, observed, hits


def group(number, hits, current, *, name='search_workspace', mutate=None):
    message, _ = call(number, name, {'chunk_ids': [hit['chunk_id'] for hit in hits]})
    output = dict(hits=copy.deepcopy(hits), passages=evidence_catalog(hits),
        passage_catalog=evidence_catalog_metadata(), snapshot_id=current['snapshot_id'],
        documents_sha256=current['documents_sha256'])
    if name == 'search_workspace': output['status'] = 'excerpts'
    if mutate: mutate(output)
    return [message, dict(role='tool', tool_call_id=message['tool_calls'][0]['id'],
        content=json.dumps(_compact_tool_output(output), ensure_ascii=False))]


def tools(hits):
    return [dict(type='function', function=dict(name=name, description=name, parameters=schema))
        for name, schema in [('search_workspace', {'type': 'object'}),
        ('read_source_passages', ReadArguments.model_json_schema()),
        ('request_clarification', {'type': 'object'}),
        ('verify_and_submit_report', selection_schema(hits))]]


def packed(exchanges, current, observed, *, window=65536):
    budget = ConversationBudget(window=window)
    messages = budget.decision_messages('untrusted source instructions', current, exchanges,
        tools(list(observed.values())), observed=observed, verified={}, phase='decision_3',
        compact_evidence=True, payload_builder=lambda messages, choices:
            workspace_action_payload(messages, choices, model='controlled'))
    return budget, messages


def results(messages):
    return [json.loads(message['content']) for message in messages if message['role'] == 'tool']


def action_names(payload):
    return [item['properties']['action']['enum'][0] for item in
        payload['response_format']['json_schema']['schema']['properties']['decision']['anyOf']]


def test_duplicate_catalog_is_referenced_without_mutating_trace_or_losing_originals():
    current, observed, hits = fixture()
    exchanges = group(1, hits, current) + group(2, hits[:1], current, name='read_source_passages')
    original = copy.deepcopy(exchanges)
    budget, messages = packed(exchanges, current, observed)
    outputs = results(messages)
    assert outputs[0]['passages'] == evidence_catalog(hits)
    assert outputs[1]['passages'] == {}
    assert set(outputs[1]['visible_passage_references']) == set(evidence_catalog(hits[:1]))
    assert all(set(item) == {'chunk_id'} for item in outputs[1]['visible_passage_references'].values())
    assert exchanges == original
    assert budget.visible_chunk_ids == list(observed)
    assert budget.calls[-1]['readable_chunk_ids'] == []
    assert budget.calls[-1]['deduplicated_passage_count'] == len(evidence_catalog(hits[:1]))
    assert 'read_source_passages' not in action_names(budget.decision_payload)
    assert set(action_names(budget.decision_payload)) == {'search_workspace', 'request_clarification', 'verify_and_submit_report'}
    assert len(complete_tool_groups(messages[2:])) == 2
    assert budget.decision_payload == workspace_action_payload(messages, budget.decision_tools, model='controlled')
    seen = {}
    for message in budget.decision_payload['messages']:
        value = json.loads(message['content']) if message['role'] == 'user' else {}
        output = value.get('untrusted_tool_result', {})
        for eid, reference in output.get('visible_passage_references', {}).items():
            assert eid in seen and reference['chunk_id'] == seen[eid]['chunk_id']
        seen.update(output.get('passages', {}))
    assert seen == evidence_catalog(hits)
    # The complete selection schema is unaffected by action availability.
    assert budget.decision_tools[-1]['function']['parameters'] == selection_schema(hits)


def test_partial_catalog_keeps_read_available_and_only_exact_repeated_passages_compact():
    current, observed, hits = fixture()
    ids = list(evidence_catalog(hits[:1]))
    def subset(output): output['passages'].pop(ids[-1])
    first = group(1, hits[:1], current, mutate=subset)
    budget, _ = packed(first, current, observed)
    assert budget.visible_chunk_ids == []
    assert budget.calls[-1]['readable_chunk_ids'] == list(observed)
    # Restoring the missing line adds it verbatim; the other two become references.
    budget, messages = packed(first + group(2, hits[:1], current, name='read_source_passages'), current, observed)
    assert budget.visible_chunk_ids == [hits[0]['chunk_id']]
    assert set(results(messages)[1]['passages']) == {ids[-1]}
    readable = next(t for t in budget.decision_tools if t['function']['name'] == 'read_source_passages')
    field = readable['function']['parameters']['properties']['chunk_ids']
    assert field['items']['enum'] == [hits[1]['chunk_id']] and field['maxItems'] == 1


@pytest.mark.parametrize('mutation', ['snapshot', 'documents_hash', 'quote', 'chunk_id', 'hit_hash', 'hit_text', 'metadata', 'error', 'failed_status'])
def test_ids_with_changed_version_or_invalid_catalog_do_not_establish_visibility(mutation):
    current, observed, hits = fixture()
    def corrupt(output):
        if mutation == 'snapshot': output['snapshot_id'] = 'workspace:old'
        elif mutation == 'documents_hash': output['documents_sha256'] = 'older-documents'
        elif mutation == 'quote': next(iter(output['passages'].values()))['quote'] = 'Counterfeit quotation.'
        elif mutation == 'chunk_id': next(iter(output['passages'].values()))['chunk_id'] = hits[1]['chunk_id']
        elif mutation == 'hit_hash': output['hits'][0]['content_sha256'] = 'bad-hash'
        elif mutation == 'hit_text':
            # _compact_tool_output normally removes hit.text; corrupt after encoding below.
            pass
        elif mutation == 'metadata': output['passage_catalog']['version'] = 'unrecognized-catalog'
        elif mutation == 'error': output['error'] = 'failed to read source'
        elif mutation == 'failed_status': output['status'] = 'provider_error'
    exchange = group(1, hits[:1], current, mutate=corrupt)
    if mutation == 'hit_text':
        value = json.loads(exchange[1]['content']); value['hits'][0]['text'] = 'Counterfeit full text.'
        exchange[1]['content'] = json.dumps(value)
    budget, _ = packed(exchange, current, observed)
    assert budget.visible_chunk_ids == []
    assert hits[0]['chunk_id'] in budget.calls[-1]['readable_chunk_ids']
    assert budget.calls[-1]['deduplicated_passage_count'] == 0


def test_same_quote_in_distinct_chunks_is_not_deduplicated_by_text():
    current, observed, hits = fixture()
    budget, messages = packed(group(1, hits[:1], current) + group(2, hits[1:], current), current, observed)
    assert budget.visible_chunk_ids == list(observed)
    assert budget.calls[-1]['deduplicated_passage_count'] == 0
    assert len([p for o in results(messages) for p in o['passages'].values()
        if p['quote'] == 'An unexplained gap remains.']) == 2


def test_ambiguous_evidence_identifier_never_counts_as_visible(monkeypatch):
    from lablens import conversation
    current, observed, hits = fixture()
    exchanges = group(1, hits, current)
    # Simulate a catalog ID collision between two canonical chunks.
    monkeypatch.setattr(conversation, 'evidence_catalog', lambda selected:
        {'collision': dict(chunk_id=selected[0]['chunk_id'], quote=selected[0]['text'])})
    projected, visibility = project_visible_passages(complete_tool_groups(exchanges), current, observed)
    assert visibility['visible_chunk_ids'] == []
    assert visibility['visible_passage_count'] == visibility['deduplicated_passage_count'] == 0
    assert projected == complete_tool_groups(exchanges)


def test_schema_index_and_preexisting_references_do_not_count_as_full_text():
    current, observed, hits = fixture()
    def reference_only(output):
        output['visible_passage_references'] = {eid: dict(chunk_id=item['chunk_id'], tool_call_id='evicted')
            for eid, item in output['passages'].items()}
        output['passages'] = {}
    budget, _ = packed(group(1, hits, current, mutate=reference_only), current, observed)
    assert budget.visible_chunk_ids == []
    assert budget.calls[-1]['readable_chunk_ids'] == list(observed)
    assert 'read_source_passages' in action_names(budget.decision_payload)


def test_eviction_rebuilds_references_and_schema_from_retained_complete_batches():
    current, observed, hits = fixture()
    # First search is intentionally expensive. Its two replies form an atomic batch.
    first = group(1, hits, current, mutate=lambda o: o.update(padding='x'*20000))
    other = group('1b', hits[1:], current)
    first[0]['tool_calls'] += other[0]['tool_calls']; first += other[1:]
    last = group(2, hits[:1], current, name='read_source_passages')
    original = copy.deepcopy(first + last)
    budget, messages = packed(first + last, current, observed, window=13000)
    record = budget.calls[-1]
    assert record['dropped_tool_exchanges'] == 1 and record['retained_tool_exchanges'] == 1
    assert len(complete_tool_groups(messages[2:])) == 1
    assert results(messages)[0]['passages'] == evidence_catalog(hits[:1])
    assert 'visible_passage_references' not in results(messages)[0]
    assert budget.visible_chunk_ids == [hits[0]['chunk_id']]
    assert record['readable_chunk_ids'] == [hits[1]['chunk_id']]
    assert record['deduplicated_passage_count'] == 0
    assert 'read_source_passages' in action_names(budget.decision_payload)
    assert first + last == original


def test_failure_without_full_catalog_remains_readable_and_native_projection_is_unchanged():
    current, observed, hits = fixture()
    exchange = group(1, hits, current, mutate=lambda o: o.update(passages={}))
    budget, _ = packed(exchange, current, observed)
    assert budget.visible_chunk_ids == []
    native = ConversationBudget()
    messages = native.decision_messages('system', current, exchange, tools(hits),
        observed=observed, verified={}, phase='decision_2')
    assert messages[2:] == exchange
    assert 'evidence_projection' not in native.calls[-1]
    assert 'read_source_passages' in [t['function']['name'] for t in native.decision_tools]


def test_real_http_schema_omits_redundant_read_and_preserves_separate_review(monkeypatch, configured_model, controlled_review):
    payloads = []
    def provider(url, **kwargs):
        payload = kwargs['json']; payloads.append(payload)
        if len(payloads) == 1:
            action, args = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            assert 'read_source_passages' not in action_names(payload)
            full = next(json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                if 'untrusted_tool_result' in (m.get('content') or ''))
            action, args = 'verify_and_submit_report', dict(assessments=[dict(statement='The measured accuracy was 86 percent.',
                verdict='supported', explanation='This observation is documented.', basis='historical_observation',
                evidence_ids=list(full['passages']))])
        return response(url, dict(decision=dict(action=action, arguments=args)))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'answered' and len(controlled_review) == 1
    assert result['usage']['model_calls'] == 3 and len(payloads) == 2 and len(seen) == 1
    assert result['agent']['budget_seconds'] == 75 and result['agent']['max_model_calls'] == 7
    assert result['agent']['verified_chunk_ids'] == [h['chunk_id'] for h in result['retrieval']['hits']]
    measured = [e['request_body_bytes'] for e in result['agent']['trace'] if e['event'] == 'request_started']
    assert measured == [request_body_bytes(payload) for payload in payloads]


def test_provider_returning_disabled_read_fails_closed_with_known_usage(monkeypatch, configured_model):
    payloads = []
    def provider(url, **kwargs):
        payload = kwargs['json']; payloads.append(payload)
        if len(payloads) == 1:
            action, args = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            assert 'read_source_passages' not in action_names(payload)
            action, args = 'read_source_passages', dict(chunk_ids=['ignored-out-of-schema'])
        return response(url, dict(decision=dict(action=action, arguments=args)))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'agent_protocol_error' and len(payloads) == 2
    assert result['usage']['model_calls'] == 2 and result['usage']['generation_tokens']['total_tokens'] == 10
    assert result['agent']['tool_calls'] == 1


def test_executor_redundant_read_is_audited_noop_without_extra_provider_call(monkeypatch, configured_model):
    class SchemaBypass(HTTPModel):
        """A deliberate model-adapter bypass exercises the second local guard."""
        def __init__(self):
            self.model = 'scripted-adapter'; self.timeout_seconds = 45; self.step = 0
        def choose_workspace_action(self, payload, *, timeout_seconds):
            self.step += 1
            if self.step == 1: return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
            full = next(json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                if 'untrusted_tool_result' in (m.get('content') or ''))
            if self.step == 2:
                assert 'read_source_passages' not in action_names(payload)
                return call(2, 'read_source_passages', dict(chunk_ids=[full['hits'][0]['chunk_id']]))
            return call(3, 'request_clarification', dict(question='Which comparison is required?'))
    model = SchemaBypass()
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(model).run(request())
    output = next(e['output'] for e in result['agent']['trace']
        if e['event'] == 'tool' and e.get('name') == 'read_source_passages')
    assert output['status'] == 'already_visible' and output['hits'] == [] and output['passages'] == {}
    assert output['already_visible_chunk_ids'] == output['chunk_ids'] and output['restored_chunk_ids'] == []
    assert result['usage']['model_calls'] == model.step == result['agent']['tool_calls'] == 3
    assert len(seen) == 1 and result['agent']['verified_chunk_ids'] == []
    assert result['status'] == 'clarification_required'


def test_mixed_read_after_real_context_eviction_restores_only_missing_chunks(monkeypatch, configured_model):
    original = ConversationBudget.decision_messages
    def bounded(self, system, current, exchanges, toolset, **kwargs):
        if kwargs['phase'] == 'decision_3':
            # Size the test window for only the newest exchange, forcing the
            # production eviction loop to remove the older full search result.
            probe = ConversationBudget()
            original(probe, system, current, exchanges[-2:], toolset, **kwargs)
            self.window = probe.calls[-1]['total_reserved_units'] + 20
        elif kwargs['phase'] == 'decision_4':
            self.window = 65536
        return original(self, system, current, exchanges, toolset, **kwargs)
    monkeypatch.setattr(ConversationBudget, 'decision_messages', bounded)
    sent, requested = [], []
    def provider(url, **kwargs):
        payload = kwargs['json']; sent.append(payload)
        if len(sent) <= 2:
            action, args = 'search_workspace', dict(query='alpha' if len(sent) == 1 else 'beta', strategy='bm25', top_k=1)
        elif len(sent) == 3:
            current = next(json.loads(m['content']) for m in payload['messages']
                if m['role'] == 'user' and 'source_visibility' in m['content'])
            visibility = current['source_visibility']
            assert len(visibility['readable_chunk_ids']) == len(visibility['visible_chunk_ids']) == 1
            requested.extend(visibility['readable_chunk_ids'] + visibility['visible_chunk_ids'])
            assert 'read_source_passages' in action_names(payload)
            # Deliberately include an already-visible ID outside the advertised
            # enum. The local executor must not echo its full text again.
            action, args = 'read_source_passages', dict(chunk_ids=requested)
        else:
            action, args = 'request_clarification', dict(question='Which metric should be compared?')
        return response(url, dict(decision=dict(action=action, arguments=args)))
    monkeypatch.setattr(httpx, 'post', provider)
    _, _, hits = fixture()
    req = request(top_k=1, documents=[dict(id=h['document_id'], title=h['title'], text=h['text'], source=h['source']) for h in hits])
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(req)
    output = next(e['output'] for e in result['agent']['trace']
        if e['event'] == 'tool' and e.get('name') == 'read_source_passages')
    assert output['status'] == 'passages_restored'
    assert output['restored_chunk_ids'] == requested[:1]
    assert output['already_visible_chunk_ids'] == requested[1:]
    assert [h['chunk_id'] for h in output['hits']] == requested[:1]
    assert result['context']['context_budget']['calls'][2]['dropped_tool_exchanges'] == 1
    assert result['usage']['model_calls'] == len(sent) == 4 and len(seen) == 2
    assert result['agent']['verified_chunk_ids'] == []
    assert result['status'] == 'clarification_required'
