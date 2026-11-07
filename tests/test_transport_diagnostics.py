"""Deterministic transport diagnostics, without model calls or new quality cases."""
from asyncio import CancelledError
from contextlib import contextmanager
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from lablens import rag, runtime_telemetry as telemetry
from lablens.cancellation import ExecutionCancelled
from lablens.model import HTTPModel, ModelError
from lablens.schemas import answer_request_schema


@contextmanager
def observed():
    events = []
    start = time.monotonic()
    recorder = telemetry.ExecutionTelemetry(events, started=start, deadline=start + 60,
                                            clock=time.monotonic)
    with recorder.bind(), recorder.stage('review', step=1):
        yield events


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('LLM_MODEL', 'controlled')
    monkeypatch.setenv('LLM_API_KEY', 'private-key')
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '5')
    return HTTPModel()


def invoke(path, model, usage):
    if path == 'complete':
        return model.complete([{'role': 'user', 'content': 'private-payload'}], [])
    if path == 'decision':
        return model.choose_workspace_action({'model': 'controlled'}, timeout_seconds=2)
    return rag._post(dict(base='https://example.invalid/v1', key='private-key',
                          timeout=2, deadline=time.monotonic() + 60),
                     '/chat/completions', {'model': 'controlled'}, usage, 'generation')


def request_end(events):
    rows = [event for event in events if event['event'] == 'request_finished']
    assert len(rows) == 1
    assert len([event for event in events if event['event'] == 'request_started']) == 1
    return rows[0]


@pytest.mark.parametrize('path', ['complete', 'decision', 'rag'])
@pytest.mark.parametrize('cls,phase', [(httpx.ConnectTimeout, 'connect'),
    (httpx.ReadTimeout, 'read'), (httpx.WriteTimeout, 'write'),
    (httpx.PoolTimeout, 'pool'), (httpx.TimeoutException, 'unknown')])
def test_timeout_classification_and_unknown_usage_survive_public_error_conversion(
        monkeypatch, model, path, cls, phase):
    calls, usage = [], rag._new_usage()
    def fail(url, **kwargs):
        calls.append(kwargs)
        raise cls('private-key https://private-proxy.invalid/private-payload')
    monkeypatch.setattr(httpx, 'post', fail)
    with observed() as events, pytest.raises((ModelError, rag.RAGError), match='timed out'):
        invoke(path, model, usage)
    row = request_end(events)
    assert len(calls) == 1
    assert row['outcome'] == 'timeout'
    assert row['timeout_phase'] == phase and row['exception_type'] == cls.__name__
    assert row['response_observation'] == 'complete_httpx_response'
    assert row['response_received'] is False and row['provider_acceptance'] == 'unknown'
    assert row['http_status'] is None and row['usage_known'] is False
    assert all(value is None for value in row['usage'].values())
    assert 'private' not in json.dumps(events)
    if path == 'rag':
        assert usage['model_calls'] == 1 and usage['provider_usage_unknown'] is True
        assert all(value is None for value in usage['generation_tokens'].values())


@pytest.mark.parametrize('path', ['complete', 'decision', 'rag'])
@pytest.mark.parametrize('failure,outcome,exception_type,received,status', [
    ('http', 'http_error', 'HTTPStatusError', True, 429),
    ('json', 'error', 'JSONDecodeError', True, 200),
    ('transport', 'transport_error', 'ConnectError', False, None),
])
def test_non_timeout_failures_do_not_fabricate_timeout_phase(
        monkeypatch, model, path, failure, outcome, exception_type, received, status):
    calls, usage = [], rag._new_usage()
    def fail(url, **kwargs):
        calls.append(kwargs)
        if failure == 'transport':
            raise httpx.ConnectError('private proxy URL and key')
        return httpx.Response(status, text='private malformed or rejected body',
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', fail)
    with observed() as events, pytest.raises((ModelError, rag.RAGError)):
        invoke(path, model, usage)
    row = request_end(events)
    assert len(calls) == 1
    assert row['outcome'] == outcome and row['exception_type'] == exception_type
    assert row['response_received'] is received and row['http_status'] == status
    assert 'timeout_phase' not in row and not row['usage_known']
    assert 'private' not in json.dumps(events)
    if path == 'rag':
        assert usage['provider_usage_unknown'] is True
        assert all(value is None for value in usage['generation_tokens'].values())


@pytest.mark.parametrize('tools,choice', [([], None),
    ([{'type': 'function', 'function': {'name': 'controlled', 'parameters': {}}}], 'required')])
def test_complete_preserves_payload_timeout_result_and_usage(monkeypatch, model, tools, choice):
    messages = [{'role': 'user', 'content': 'private-payload'}]
    usage = dict(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    calls = []
    def reply(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'choices': [{'message': {'role': 'assistant', 'content': 'private-reply'},
                         'finish_reason': 'stop'}], 'usage': usage})
    monkeypatch.setattr(httpx, 'post', reply)
    with observed() as events:
        message, actual_usage = model.complete(messages, tools, timeout_seconds=2, tool_choice=choice)
    expected = dict(model='controlled', messages=messages, temperature=0, max_tokens=2600)
    if tools:
        expected.update(tools=tools, tool_choice=choice)
    else:
        expected['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'answer', 'strict': True, 'schema': answer_request_schema()}}
    assert calls == [('https://example.invalid/v1/chat/completions', dict(
        timeout=2, headers={'Authorization': 'Bearer private-key'}, json=expected))]
    assert message == dict(role='assistant', content='private-reply', finish_reason='stop')
    assert actual_usage == usage
    row = request_end(events)
    assert row['outcome'] == 'returned' and row['usage'] == usage and row['usage_known'] is True
    assert row['response_received'] is True and row['provider_acceptance'] == 'response_received'
    assert row['request_body_bytes'] == telemetry.request_body_bytes(expected)
    assert 'exception_type' not in row and 'timeout_phase' not in row
    assert 'private' not in json.dumps(events)


@pytest.mark.parametrize('exc', [ExecutionCancelled('private'), CancelledError('private'),
                                KeyboardInterrupt('private')])
def test_cancellation_is_recorded_and_original_exception_rethrown(exc):
    with observed() as events, pytest.raises(type(exc)) as captured:
        with telemetry.observe_request({'model': 'controlled'}, 1):
            raise exc
    assert captured.value is exc
    row = request_end(events)
    assert row['outcome'] == 'cancelled' and row['exception_type'] == type(exc).__name__
    assert 'timeout_phase' not in row and row['usage_known'] is False
    assert 'private' not in json.dumps(events)


def test_unknown_exception_class_name_is_not_recorded():
    private_exception = type('private_key_proxy_hostname', (httpx.ReadTimeout,), {})
    with observed() as events, pytest.raises(private_exception):
        with telemetry.observe_request({}, 1):
            raise private_exception('private message')
    row = request_end(events)
    assert row['exception_type'] == 'unknown' and row['timeout_phase'] == 'read'
    assert 'private' not in json.dumps(events)


@pytest.mark.parametrize('point', ['start', 'finish'])
@pytest.mark.parametrize('business_error', [False, True])
def test_ordinary_observer_faults_do_not_replace_success_or_business_error(
        monkeypatch, point, business_error):
    def broken(*args, **kwargs):
        raise RuntimeError('private observer failure')
    if point == 'start':
        monkeypatch.setattr(telemetry, 'request_body_bytes', broken)
    else:
        monkeypatch.setattr(telemetry.RequestObservation, 'finish', broken)
    error = httpx.ReadTimeout('private business error')
    entered = []
    def run():
        with observed(), telemetry.observe_request({}, 1) as observation:
            entered.append(True)
            if business_error:
                raise error
            observation.received(httpx.Response(200), {'usage': {}})
        return 'success'
    if business_error:
        with pytest.raises(httpx.ReadTimeout) as captured:
            run()
        assert captured.value is error
    else:
        assert run() == 'success'
    assert entered == [True]


def test_received_observer_fault_does_not_change_application_result():
    class BrokenDiagnosticResponse:
        @property
        def status_code(self):
            raise ValueError('private diagnostic failure')
    with observed() as events, telemetry.observe_request({}, 1) as observation:
        observation.received(BrokenDiagnosticResponse())
    row = request_end(events)
    assert row['outcome'] == 'returned' and row['response_received'] is True
    assert row['http_status'] is None and row['usage_known'] is False


def test_response_after_total_deadline_keeps_known_usage_without_inventing_transport_timeout(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(rag, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    calls, usage = [], rag._new_usage()
    def reply(url, **kwargs):
        calls.append(kwargs)
        now[0] = 21.0
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'usage': dict(prompt_tokens=7, completion_tokens=3, total_tokens=10)})
    monkeypatch.setattr(httpx, 'post', reply)
    with observed() as events, pytest.raises(rag.RAGError, match='after the provider response'):
        rag._post(dict(base='https://example.invalid', key='private', timeout=5, deadline=20),
                  '/chat/completions', {}, usage, 'generation')
    row = request_end(events)
    assert len(calls) == usage['model_calls'] == 1 and usage['budget_exhausted'] is True
    assert row['response_received'] is True and row['usage_known'] is True
    assert row['usage']['total_tokens'] == usage['generation_tokens']['total_tokens'] == 10
    assert row['outcome'] == 'error' and 'timeout_phase' not in row
    assert row['exception_type'] == 'unknown'  # application RAGError, not a transport class
