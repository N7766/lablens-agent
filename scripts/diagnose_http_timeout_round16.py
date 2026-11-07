#!/usr/bin/env python3
"""Real loopback-only timing probes. Never reads model credentials or calls a provider."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from lablens.cancellation import CancellationRegistry
from lablens.model import HTTPModel, ModelError, workspace_action_payload
from lablens.runtime_telemetry import ExecutionTelemetry
from lablens.workspace_agent import WorkspaceAgent, WorkspaceAgentRequest

PROTOCOL = ROOT / 'docs/evaluations/conversation-round16/timeout-protocol.md'
SCENARIOS = ('delayed_headers', 'body_stall', 'slow_trickle')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Timeline:
    def __init__(self):
        self.started = time.monotonic()
        self.events = []
        self.lock = threading.Lock()

    def add(self, actor, event, **fields):
        with self.lock:
            row = dict(actor=actor, event=event,
                       elapsed_ms=round((time.monotonic() - self.started) * 1000, 3), **fields)
            self.events.append(row)
            return row


@contextmanager
def controlled_server(timeline, scenario, *, chunks=8, gap=.08):
    """Only accepts this known fixture path, logs metadata rather than payloads."""
    first_body = threading.Event()
    finished = threading.Event()
    seen = []
    body = json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(role='assistant',
        content=json.dumps(dict(decision=dict(action='search_workspace',
            arguments=dict(query='baseline', strategy='bm25', top_k=1))))))],
        usage=dict(prompt_tokens=4, completion_tokens=3, total_tokens=7))).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if size > 100000:
                self.send_error(413)
                return
            self.rfile.read(size)
            seen.append(1)
            timeline.add('server', 'request_body_received', request_body_bytes=size)
            try:
                if scenario == 'delayed_headers':
                    time.sleep(.45)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                timeline.add('server', 'headers_flushed')
                if scenario == 'body_stall':
                    parts = (body[:8], body[8:])
                elif scenario == 'slow_trickle':
                    parts = tuple(body[len(body)*i//chunks:len(body)*(i+1)//chunks] for i in range(chunks))
                else:
                    parts = (body,)
                for index, part in enumerate(parts):
                    if index:
                        time.sleep(.45 if scenario == 'body_stall' else gap)
                    self.wfile.write(part)
                    self.wfile.flush()
                    timeline.add('server', 'body_chunk_flushed', index=index, bytes=len(part))
                    first_body.set()
                timeline.add('server', 'body_complete')
            except (BrokenPipeError, ConnectionResetError):
                timeline.add('server', 'client_connection_closed')
            finally:
                finished.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', seen, first_body
    finally:
        finished.wait(3)
        server.shutdown()
        server.server_close()
        thread.join(2)


def local_model(base):
    # __init__ loads dotenv. Avoid it: this probe's model and URL are entirely local.
    model = HTTPModel.__new__(HTTPModel)
    model.base, model.model, model.key = base, 'controlled-local-fixture', 'local-fixture-placeholder'
    model.timeout_seconds = 2
    return model


def fixture_payload():
    return workspace_action_payload([dict(role='user', content='Find the baseline.')],
        [dict(type='function', function=dict(name='search_workspace', description='Local fixture.',
            parameters=dict(type='object', properties={}, additionalProperties=True)))],
        model='controlled-local-fixture')


def observed_probe(scenario):
    timeline = Timeline()
    result = dict(kind='stream_observer', scenario=scenario, read_timeout_ms=200,
                  headers_observed=False, first_body_chunk_observed=False, complete_response=False,
                  usage_kind='synthetic_fixture', upstream_billing='not_applicable_local_fixture')

    def trace(name, info):
        # Never retain info: it can contain credentials, request bodies or arbitrary exceptions.
        timeline.add('transport', name)

    with controlled_server(timeline, scenario) as (base, seen, _):
        with httpx.Client(trust_env=False, timeout=.2) as client:
            started = time.monotonic()
            timeline.add('client', 'request_started')
            try:
                with client.stream('POST', base + '/chat/completions', json=fixture_payload(),
                                   extensions={'trace': trace}) as response:
                    result['headers_observed'] = True
                    result['headers_elapsed_ms'] = timeline.add('client', 'headers_observed')['elapsed_ms']
                    for chunk in response.iter_raw():
                        if not result['first_body_chunk_observed']:
                            result['first_body_chunk_observed'] = True
                            result['first_body_chunk_elapsed_ms'] = timeline.add(
                                'client', 'first_body_chunk_observed', bytes=len(chunk))['elapsed_ms']
                    result['complete_response'] = True
                    result['outcome'] = 'returned'
            except httpx.ReadTimeout:
                result.update(outcome='read_timeout', timeout_phase='read')
            result['request_elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
            timeline.add('client', 'request_finished', outcome=result['outcome'])
        result['server_requests'] = len(seen)
    result['events'] = timeline.events
    return result


def model_probe(scenario):
    timeline, events = Timeline(), []
    result = dict(kind='existing_http_model', scenario=scenario, assigned_timeout_ms=200,
                  usage_kind='synthetic_fixture', upstream_billing='not_applicable_local_fixture')
    with controlled_server(timeline, scenario) as (base, seen, _):
        started = time.monotonic()
        telemetry = ExecutionTelemetry(events, started=started, deadline=started + .2, clock=time.monotonic)
        with telemetry.bind(), telemetry.stage('decision', step=1):
            try:
                _, usage = local_model(base).choose_workspace_action(fixture_payload(), timeout_seconds=.2)
                result.update(outcome='returned', usage=usage)
            except ModelError as exc:
                result.update(outcome='model_error', error=str(exc))
        result['request_elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        result['server_requests'] = len(seen)
    result.update(events=timeline.events, runtime_telemetry=events)
    return result


def agent_probe(*, cancel=False):
    timeline = Timeline()
    registry = CancellationRegistry()
    task_id = 'round16-controlled-cancellation' if cancel else 'round16-controlled-deadline'
    result = dict(kind='existing_workspace_agent', scenario='cancel_during_body' if cancel else 'absolute_deadline',
                  configured_agent_budget_ms=1000, usage_kind='synthetic_fixture',
                  upstream_billing='not_applicable_local_fixture')
    cancel_thread = None
    with controlled_server(timeline, 'slow_trickle', chunks=16, gap=.1) as (base, seen, first_body):
        def request_cancel():
            if first_body.wait(3):
                time.sleep(.05)
                result['cancel_ack_during_request'] = registry.cancel(task_id)
                timeline.add('control', 'cancellation_requested', stopped=result['cancel_ack_during_request']['stopped'])

        request = WorkspaceAgentRequest(question='Find the SFT baseline evidence.', snapshot_id='local-fixture:1',
            documents=[dict(id='baseline', title='Baseline', text='The SFT baseline exists.', source='controlled-fixture')],
            strategy='bm25', top_k=1)
        with patch.dict('os.environ', {'LABLENS_AGENT_TOTAL_SECONDS': '1',
                'LABLENS_AGENT_TOOL_BASE_URL': base.removesuffix('/v1'),
                'LABLENS_AUTH_ENABLED': 'true',
                'LABLENS_SERVICE_KEY': 'round16-fixed-synthetic-service-identity-only',
                'LABLENS_CONTEXT_WINDOW_UNITS': '65536'}):
            started = time.monotonic()
            with registry.execution(task_id):
                if cancel:
                    cancel_thread = threading.Thread(target=request_cancel, daemon=True)
                    cancel_thread.start()
                answer = WorkspaceAgent(local_model(base)).run(request)
            result['request_elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        if cancel_thread:
            cancel_thread.join(3)
            result['cancel_ack_after_exit'] = registry.cancel(task_id)
        result.update(server_requests=len(seen), agent_status=answer['status'], usage=answer['usage'],
                      model_calls=answer['usage']['model_calls'], tool_calls=answer['agent']['tool_calls'],
                      runtime_telemetry=answer['agent']['trace'])
    result['events'] = timeline.events
    return result


def validate(results):
    """Evidence checks preserve observed limits; passing means reproduced, not repaired."""
    by = {(row['kind'], row['scenario']): row for row in results}
    assert len(results) == 8
    assert all(row['server_requests'] == 1 for row in results), 'Each scenario must send exactly one request.'
    head, body, trickle = (by['stream_observer', key] for key in SCENARIOS)
    assert head['outcome'] == body['outcome'] == 'read_timeout'
    assert not head['headers_observed'] and not head['first_body_chunk_observed']
    assert body['headers_observed'] and body['first_body_chunk_observed']
    assert not head['complete_response'] and not body['complete_response']
    assert trickle['complete_response'] and trickle['request_elapsed_ms'] > 200
    for scenario in SCENARIOS[:2]:
        row = by['existing_http_model', scenario]
        finish = next(event for event in row['runtime_telemetry'] if event['event'] == 'request_finished')
        assert row['outcome'] == 'model_error' and finish['timeout_phase'] == 'read'
        assert not finish['response_received'] and not finish['usage_known']
        assert finish['provider_acceptance'] == 'unknown'
    model = by['existing_http_model', 'slow_trickle']
    assert model['outcome'] == 'returned' and model['request_elapsed_ms'] > model['assigned_timeout_ms']
    deadline = by['existing_workspace_agent', 'absolute_deadline']
    assert deadline['agent_status'] == 'agent_timeout' and deadline['request_elapsed_ms'] > 1000
    assert deadline['usage']['budget_exhausted'] and deadline['model_calls'] == 1 and deadline['tool_calls'] == 0
    cancel = by['existing_workspace_agent', 'cancel_during_body']
    assert cancel['agent_status'] == 'agent_cancelled'
    assert cancel['cancel_ack_during_request']['stopped'] is False
    assert cancel['cancel_ack_after_exit']['stopped'] is True
    assert cancel['model_calls'] == 1 and cancel['tool_calls'] == 0
    return dict(status='reproduced', checks='8 scenarios; one request each; no provider calls or automatic retries',
                limitations_confirmed=['per-read timeout is not absolute request deadline',
                    'Agent checks total deadline after blocking HTTP returns',
                    'cooperative cancellation waits for in-flight model HTTP to return',
                    'complete-response telemetry cannot distinguish headers wait from body stall'])


def run_probes():
    # Both casings avoid local shell proxy configuration changing this controlled experiment.
    with patch.dict('os.environ', {'NO_PROXY': '127.0.0.1,localhost,::1', 'no_proxy': '127.0.0.1,localhost,::1'}):
        return ([observed_probe(name) for name in SCENARIOS]
                + [model_probe(name) for name in SCENARIOS]
                + [agent_probe(), agent_probe(cancel=True)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if not PROTOCOL.is_file():
        parser.error('Freeze the checked-in timeout protocol before running.')
    # Exclusive directory creation keeps original failures and raw evidence intact.
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = [PROTOCOL, Path(__file__).resolve(), ROOT / 'lablens/model.py',
              ROOT / 'lablens/runtime_telemetry.py', ROOT / 'lablens/workspace_agent.py', ROOT / 'lablens/cancellation.py',
              ROOT / 'lablens/service_auth.py']
    manifest = dict(started_at=datetime.now(timezone.utc).isoformat(), provider_requests=0,
        versions=dict(python=platform.python_version(), httpx=httpx.__version__,
                      httpcore=importlib.metadata.version('httpcore')),
        sources={str(path.relative_to(ROOT)): sha256(path) for path in inputs})
    (args.output / 'source-freeze.json').write_text(json.dumps(manifest, indent=2) + '\n')
    results = run_probes()
    (args.output / 'raw-results.json').write_text(json.dumps(results, indent=2) + '\n')
    summary = validate(results)
    summary.update(finished_at=datetime.now(timezone.utc).isoformat(), provider_requests=0,
                   source_changed_during_probe=[str(path.relative_to(ROOT)) for path in inputs
                       if manifest['sources'][str(path.relative_to(ROOT))] != sha256(path)],
                   results_sha256=sha256(args.output / 'raw-results.json'))
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
