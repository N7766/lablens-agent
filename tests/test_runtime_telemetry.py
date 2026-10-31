"""Real loopback HTTP and delays; controlled providers, never model-quality claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import threading
import time

import httpx
import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections
from lablens.cancellation import CancellationRegistry
from lablens.model import HTTPModel
from lablens.runtime_telemetry import (ExecutionTelemetry, observe_request, request_body_bytes, safe_usage)
from lablens.workspace_agent import WorkspaceAgent
from test_workspace_agent import request, retrieval_server, call


@contextmanager
def provider_server(*, decision_delay=0.01, review_delay=0.01, release_review=None, usage=True):
    seen, entered_review = [], threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(raw)
            review = payload['response_format']['json_schema']['name'] == 'evidence_assessments'
            seen.append(dict(phase='review' if review else 'decision', body_bytes=len(raw), payload=payload))
            if review:
                entered_review.set()
                if release_review is not None:
                    assert release_review.wait(5)
                time.sleep(review_delay)
                data = json.loads(payload['messages'][-1]['content'])
                reply = data['untrusted_draft']
            else:
                time.sleep(decision_delay)
                outputs = [json.loads(message['content'])['untrusted_tool_result']
                    for message in payload['messages'] if message['role'] == 'user'
                    and 'untrusted_tool_result' in message['content']]
                if not outputs:
                    action, args = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
                else:
                    action = 'verify_and_submit_report'
                    args = dict(assessments=[dict(statement='The measured accuracy was 86 percent.',
                        verdict='supported', explanation='The supplied local fixture documents 86 percent.',
                        basis='historical_observation', evidence_ids=list(outputs[-1]['passages']))])
                reply = dict(decision=dict(action=action, arguments=args))
            body = dict(choices=[dict(message=dict(role='assistant', content=json.dumps(reply)), finish_reason='stop')])
            if usage:
                body['usage'] = dict(prompt_tokens=13, completion_tokens=7, total_tokens=20)
            encoded = json.dumps(body).encode()
            try:
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded))); self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the real client times out before a controlled response.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', seen, entered_review
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def model_environment(monkeypatch, base):
    monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1,::1')
    monkeypatch.setenv('no_proxy', 'localhost,127.0.0.1,::1')
    monkeypatch.setenv('LLM_BASE_URL', base)
    monkeypatch.setenv('LLM_API_KEY', 'controlled-secret-must-not-appear')
    monkeypatch.setenv('LLM_MODEL', 'controlled-http-provider')
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '1')
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '1')
    monkeypatch.setenv('RAG_TOTAL_SECONDS', '80')
    monkeypatch.setenv('LABLENS_AGENT_TOTAL_SECONDS', '75')
    monkeypatch.setenv('LABLENS_CONTEXT_WINDOW_UNITS', '65536')
    return HTTPModel()


def finished(result, phase=None, event='request_finished'):
    return [item for item in result['agent']['trace'] if item['event'] == event
            and (phase is None or item['phase'] == phase)]


def assert_paired(result):
    trace = result['agent']['trace']
    for prefix, key in [('stage', 'stage_id'), ('request', 'request_id')]:
        starts = {event[key]: event for event in trace if event['event'] == prefix+'_started'}
        ends = {event[key]: event for event in trace if event['event'] == prefix+'_finished'}
        assert starts.keys() == ends.keys()
        for identity, start in starts.items():
            end = ends[identity]
            assert end['elapsed_ms'] >= start['elapsed_ms']
            assert 0 <= end['remaining_ms'] <= start['remaining_ms'] <= 75000
            assert end['duration_ms'] >= 0
    assert len(trace) <= 128
    telemetry = [event for event in trace if event['event'].startswith(('stage_', 'request_'))]
    serialized = json.dumps(telemetry)
    assert 'controlled-secret-must-not-appear' not in serialized
    assert 'Authorization' not in serialized and 'messages' not in serialized
    assert 'The supplied local fixture' not in serialized


def test_real_http_trace_has_exact_request_bytes_nested_stages_and_response_usage(monkeypatch):
    with provider_server() as (provider, seen, _), retrieval_server() as (tools, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', tools)
        result = WorkspaceAgent(model_environment(monkeypatch, provider)).run(request())
    assert result['status'] == 'answered'
    assert result['agent']['version'] == 'workspace-agent-v7'
    assert result['agent']['telemetry_version'] == 'runtime-stages-v1'
    assert result['agent']['budget_seconds'] == 75
    assert result['agent']['max_model_calls'] == 7
    assert result['usage']['model_calls'] == 3 and len(seen) == 3
    calls = finished(result)
    assert [item['request_body_bytes'] for item in calls] == [item['body_bytes'] for item in seen]
    assert all(item['usage_known'] and item['usage']['total_tokens'] == 20 for item in calls)
    assert all(item['outcome'] == 'returned' and item['timeout_ms'] == 1000 for item in calls)
    assert all(item['duration_ms'] >= 8 for item in calls)
    stages = finished(result, event='stage_finished')
    review = next(item for item in stages if item['phase'] == 'review')
    parent = next(item for item in stages if item['stage_id'] == review['parent_stage_id'])
    assert parent['name'] == 'verify_and_submit_report' and parent['duration_ms'] >= review['duration_ms']
    tool = next(item for item in stages if item.get('name') == 'search_workspace')
    assert tool['request_body_bytes'] > 0 and 0 < tool['timeout_ms'] <= 75000
    assert_paired(result)


def test_real_http_review_timeout_retains_known_decisions_and_unknown_review_without_retry(monkeypatch):
    with provider_server(review_delay=1.2) as (provider, seen, _), retrieval_server() as (tools, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', tools)
        result = WorkspaceAgent(model_environment(monkeypatch, provider)).run(request())
    assert result['status'] == 'provider_error'  # per-call timeout, total budget remains
    assert result['answer'] == '' and result['usage']['model_calls'] == len(seen) == 3
    assert result['usage']['generation_tokens']['total_tokens'] is None
    assert result['usage']['provider_usage_unknown'] is True
    assert [item['usage']['total_tokens'] for item in finished(result)] == [20, 20, None]
    review = finished(result, 'review')[0]
    assert review['outcome'] == 'timeout' and review['duration_ms'] >= 950
    assert review['response_received'] is False and review['provider_acceptance'] == 'unknown'
    assert review['usage_known'] is False and review['remaining_ms'] > 0
    assert finished(result, 'review', 'stage_finished')[0]['outcome'] == 'timeout'
    assert_paired(result)


def test_real_http_decision_timeout_has_terminal_stage_no_tool_no_retry(monkeypatch):
    with provider_server(decision_delay=1.2) as (provider, seen, _):
        result = WorkspaceAgent(model_environment(monkeypatch, provider)).run(request())
    assert result['status'] == 'agent_timeout' and result['usage']['model_calls'] == len(seen) == 1
    assert result['agent']['tool_calls'] == 0
    assert finished(result)[0]['outcome'] == 'timeout'
    assert finished(result, 'decision', 'stage_finished')[0]['outcome'] == 'timeout'
    assert_paired(result)


def test_cancel_during_real_review_records_response_usage_and_closes_every_stage(monkeypatch):
    release, registry = threading.Event(), CancellationRegistry()
    with provider_server(release_review=release) as (provider, seen, entered), retrieval_server() as (tools, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', tools)
        model = model_environment(monkeypatch, provider)
        def run():
            with registry.execution('runtime-controlled-cancel') as execution:
                execution.result = WorkspaceAgent(model).run(request())
                return execution.result
        with ThreadPoolExecutor() as pool:
            future = pool.submit(run)
            assert entered.wait(4)
            assert registry.cancel('runtime-controlled-cancel')['stopped'] is False
            release.set()
            result = future.result(4)
    assert result['status'] == 'agent_cancelled' and result['answer'] == ''
    assert len(seen) == 3 and result['usage']['generation_tokens']['total_tokens'] == 60
    review = finished(result, 'review')[0]
    assert review['outcome'] == 'cancelled' and review['usage_known'] is True
    assert review['response_received'] is True and review['usage']['total_tokens'] == 20
    assert all(item['outcome'] == 'cancelled' for item in finished(result, event='stage_finished')
               if item['phase'] == 'review' or item.get('name') == 'verify_and_submit_report')
    assert registry.cancel('runtime-controlled-cancel')['stopped'] is True
    assert_paired(result)


def test_missing_provider_usage_stays_unknown_even_on_success(monkeypatch):
    with provider_server(usage=False) as (provider, seen, _), retrieval_server() as (tools, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', tools)
        result = WorkspaceAgent(model_environment(monkeypatch, provider)).run(request())
    assert result['status'] == 'answered' and len(seen) == 3
    assert result['usage']['generation_tokens']['total_tokens'] is None
    assert all(not item['usage_known'] and all(value is None for value in item['usage'].values())
               for item in finished(result))
    assert_paired(result)


def test_compact_review_is_lossless_for_catalog_draft_and_preserves_all_checks():
    hits = rag.chunk_documents(request().documents)
    catalog = evidence_catalog(hits)
    selection = dict(assessments=[dict(statement='The measured accuracy was 86 percent.', verdict='supported',
        explanation='The report records 86 percent.', basis='historical_observation', evidence_ids=list(catalog))])
    raw = resolve_selections(selection, hits)
    contract = make_review_contract('What was observed?', raw)
    context = dict(current_question='What was observed?', scoped_review_contract=contract,
                   pinned_state={'constraints': ['local/synthetic only']}, untrusted_recent_answers=[])
    original = copy.deepcopy((raw, hits, context))
    before = rag._generation_payload('What was observed?', hits, {'model': 'controlled'}, draft=raw,
        context_data=context, compact_review=False)
    after = rag._generation_payload('What was observed?', hits, {'model': 'controlled'}, draft=raw, context_data=context)
    old, new = [json.loads(payload['messages'][1]['content']) for payload in (before, after)]
    assert (raw, hits, context) == original
    assert new['passages'] == old['passages'] == catalog and new['evidence'] == old['evidence']
    assert resolve_selections(new['untrusted_draft'], hits) == raw
    assert new['untrusted_conversation_context']['pinned_state'] == context['pinned_state']
    assert 'current_question' not in new['untrusted_conversation_context']
    assert new['server_review_contract'] == {key: value for key, value in contract.items() if key != 'statements'}
    assert after['response_format'] == before['response_format']
    assert request_body_bytes(after) < request_body_bytes(before)


def test_non_catalog_draft_retains_quotes_and_nonidentical_current_question():
    hits = rag.chunk_documents(request().documents)
    raw = {'assessments': [dict(statement='Accuracy', verdict='supported', explanation='Observed.',
        basis='historical_observation', citation_ids=[hits[0]['chunk_id']],
        quotes=[dict(chunk_id=hits[0]['chunk_id'], quote='measured accuracy')])]}
    context = dict(current_question='A genuinely different recorded question',
                   scoped_review_contract=make_review_contract('Accuracy?', raw))
    payload = rag._generation_payload('Accuracy?', hits, {'model': 'controlled'}, draft=raw, context_data=context)
    data = json.loads(payload['messages'][1]['content'])
    assert data['untrusted_draft'] == raw
    assert data['untrusted_conversation_context']['current_question'] == context['current_question']


@pytest.mark.parametrize('exc,outcome', [(httpx.ReadTimeout('private details'), 'timeout'),
    (httpx.ConnectError('private details'), 'transport_error'), (ValueError('private details'), 'error')])
def test_request_error_details_and_credentials_never_enter_trace(exc, outcome):
    events = []
    start = time.monotonic()
    telemetry = ExecutionTelemetry(events, started=start, deadline=start+75, clock=time.monotonic)
    with telemetry.bind(), pytest.raises(type(exc)), telemetry.stage('review', step=2):
        with observe_request({'messages': [{'content': 'private payload'}]}, 1):
            raise exc
    assert events[-2]['event'] == 'request_finished' and events[-2]['outcome'] == outcome
    assert 'private' not in json.dumps(events)
    assert events[-2]['usage_known'] is False


def test_trace_bound_at_maximum_native_steps_and_tool_calls(monkeypatch):
    class ManyCalls:
        def __init__(self): self.step = 0
        def complete(self, messages, tools):
            self.step += 1
            # Invalid read-only arguments exercise every continuation without a provider.
            count = 2 if self.step <= 4 else 1
            calls = [call(f'{self.step}-{index}', 'read_source_passages', {'chunk_ids': ['not-observed']})[0]['tool_calls'][0]
                     for index in range(count)]
            return {'role': 'assistant', 'tool_calls': calls}, dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    result = WorkspaceAgent(ManyCalls(), max_steps=8, max_calls=12).run(request())
    assert result['agent']['steps'] == 8 and result['agent']['tool_calls'] == 12
    assert result['status'] == 'agent_budget_exceeded'
    assert_paired(result)
    # An HTTP decision adds exactly two request events; one nested review adds
    # its pair plus one request pair. Even that upper bound stays below storage.
    assert len(result['agent']['trace']) + 2*8 + 8 <= 128


def test_boolean_and_negative_usage_are_unknown_and_real_zero_is_known():
    assert safe_usage({'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': 0}) == {
        'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': 0}


def test_real_review_total_deadline_exhaustion_records_reduced_actual_timeout(monkeypatch):
    with provider_server(review_delay=1.2) as (provider, seen, _), retrieval_server() as (tools, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', tools)
        model = model_environment(monkeypatch, provider)
        monkeypatch.setenv('LABLENS_AGENT_TOTAL_SECONDS', '1')
        result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_timeout' and result['usage']['budget_exhausted'] is True
    assert result['answer'] == '' and result['usage']['model_calls'] == len(seen) == 3
    review = finished(result, 'review')[0]
    assert review['outcome'] == 'timeout' and 0 < review['timeout_ms'] < 1000
    assert review['remaining_ms'] == 0 and review['usage']['total_tokens'] is None
    assert finished(result, 'review', 'stage_finished')[0]['outcome'] == 'timeout'
    assert_paired(result)
