#!/usr/bin/env python3
"""Once-only local HTTP deadline comparison; no provider traffic or credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

import httpx

from diagnose_http_timeout_round16 import (Timeline, controlled_server, local_model,
    fixture_payload, sha256)
from lablens.bounded_http import post
from lablens.cancellation import CancellationRegistry
from lablens.model import ModelError
from lablens.runtime_telemetry import ExecutionTelemetry, observe_request
from lablens.workspace_agent import WorkspaceAgent, WorkspaceAgentRequest

PROTOCOL = ROOT / 'docs/evaluations/conversation-round17/timeout-protocol.md'


def probe(kind, scenario, *, timeout=.2, read_timeout=None):
    timeline, events = Timeline(), []
    result = dict(kind=kind, scenario=scenario, assigned_timeout_ms=timeout*1000,
        read_timeout_ms=None if read_timeout is None else read_timeout*1000,
        usage_kind='synthetic_fixture', upstream_billing='not_applicable_local_fixture')
    with controlled_server(timeline, scenario) as (base, seen, _):
        started = time.monotonic()
        telemetry = ExecutionTelemetry(events, started=started, deadline=started+timeout, clock=time.monotonic)
        with telemetry.bind(), telemetry.stage('decision', step=1):
            try:
                if kind == 'complete':
                    _, usage = local_model(base).complete([], [], timeout_seconds=timeout)
                elif kind == 'decision':
                    _, usage = local_model(base).choose_workspace_action(fixture_payload(), timeout_seconds=timeout)
                else:
                    with observe_request(fixture_payload(), timeout) as observation:
                        response = post(base + '/chat/completions', json=fixture_payload(),
                            headers={}, timeout=timeout, read_timeout=read_timeout)
                        body = response.json()
                        observation.received(response, body)
                        usage = body['usage']
                result.update(outcome='returned', usage=usage)
            except (ModelError, httpx.TimeoutException) as exc:
                result.update(outcome='timeout', exception_type=type(exc).__name__)
        result['request_elapsed_ms'] = round((time.monotonic()-started)*1000, 3)
        result['server_requests'] = len(seen)
    result.update(events=timeline.events, runtime_telemetry=events)
    return result


def agent_probe(*, cancel=False):
    timeline, registry, cancel_at = Timeline(), CancellationRegistry(), []
    task_id = 'round17-controlled-cancellation' if cancel else 'round17-controlled-deadline'
    result = dict(kind='workspace_agent', scenario='cancel_during_body' if cancel else 'absolute_deadline',
        configured_agent_budget_ms=1000, usage_kind='synthetic_fixture',
        upstream_billing='not_applicable_local_fixture')
    with controlled_server(timeline, 'slow_trickle', chunks=16, gap=.1) as (base, seen, first_body):
        def request_cancel():
            if first_body.wait(3):
                time.sleep(.05)
                cancel_at.append(time.monotonic())
                result['cancel_ack_during_request'] = registry.cancel(task_id)
                timeline.add('control', 'cancellation_requested', stopped=result['cancel_ack_during_request']['stopped'])

        request = WorkspaceAgentRequest(question='Find the SFT baseline evidence.', snapshot_id='local-fixture:1',
            documents=[dict(id='baseline', title='Baseline', text='The SFT baseline exists.', source='controlled-fixture')],
            strategy='bm25', top_k=1)
        with patch.dict('os.environ', {'LABLENS_AGENT_TOTAL_SECONDS': '1',
                'LABLENS_AGENT_TOOL_BASE_URL': base.removesuffix('/v1'), 'LABLENS_AUTH_ENABLED': 'true',
                'LABLENS_SERVICE_KEY': 'round17-fixed-synthetic-service-identity-only',
                'LABLENS_CONTEXT_WINDOW_UNITS': '65536'}):
            started = time.monotonic()
            timeline.add('client', 'request_started')
            with registry.execution(task_id):
                if cancel:
                    cancel_thread = threading.Thread(target=request_cancel)
                    cancel_thread.start()
                answer = WorkspaceAgent(local_model(base)).run(request)
            ended = time.monotonic()
            timeline.add('client', 'request_finished', status=answer['status'])
            result['request_elapsed_ms'] = round((ended-started)*1000, 3)
        if cancel:
            cancel_thread.join(3)
            assert not cancel_thread.is_alive()
            result['cancel_ack_after_exit'] = registry.cancel(task_id)
            result['cancel_to_exit_ms'] = round((ended-cancel_at[0])*1000, 3)
        result.update(server_requests=len(seen), agent_status=answer['status'], usage=answer['usage'],
            model_calls=answer['usage']['model_calls'], tool_calls=answer['agent']['tool_calls'],
            runtime_telemetry=answer['agent']['trace'])
    result['events'] = timeline.events
    return result


def run_probes():
    with patch.dict('os.environ', {'NO_PROXY': '127.0.0.1,localhost,::1', 'no_proxy': '127.0.0.1,localhost,::1'}):
        return [probe('decision', 'slow_trickle'), probe('complete', 'slow_trickle'),
            probe('transport', 'delayed_headers', timeout=.6, read_timeout=.15),
            probe('transport', 'body_stall', timeout=.6, read_timeout=.15),
            agent_probe(), agent_probe(cancel=True), probe('decision', 'slow_trickle', timeout=1)]


def validate(results):
    assert len(results) == 7
    assert all(row['server_requests'] == 1 for row in results)
    for row in results[:4]:
        finish = next(event for event in row['runtime_telemetry'] if event['event'] == 'request_finished')
        assert row['outcome'] == 'timeout'
        deadline = row['kind'] != 'transport'
        assert finish['timeout_phase'] == ('deadline' if deadline else 'read')
        assert finish['exception_type'] == ('HTTPDeadlineExceeded' if deadline else 'ReadTimeout')
        assert row['request_elapsed_ms'] <= 450
        assert finish['response_received'] is False and finish['usage_known'] is False
        assert finish['provider_acceptance'] == 'unknown'
    deadline, cancel, success = results[4:]
    assert deadline['agent_status'] == 'agent_timeout' and deadline['request_elapsed_ms'] <= 1300
    assert cancel['agent_status'] == 'agent_cancelled'
    assert cancel['cancel_ack_during_request']['stopped'] is False
    assert cancel['cancel_ack_after_exit']['stopped'] is True
    assert 0 <= cancel['cancel_to_exit_ms'] <= 300
    for row in (deadline, cancel):
        assert row['model_calls'] == 1 and row['tool_calls'] == 0
        assert row['usage']['provider_usage_unknown'] is True
        assert any(event['event'] == 'client_connection_closed' for event in row['events'])
    assert success['outcome'] == 'returned' and success['usage']['total_tokens'] == 7
    return dict(status='passed', scenarios=7, provider_requests=0,
        boundary='Local engineering regression; operating-system DNS and teardown are not a universal real-time guarantee.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if not PROTOCOL.is_file():
        parser.error('Freeze protocol before running')
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = [PROTOCOL, Path(__file__).resolve(), ROOT / 'scripts/diagnose_http_timeout_round16.py',
        *sorted((ROOT / 'lablens').glob('*.py'))]
    freeze = dict(started_at=datetime.now(timezone.utc).isoformat(), provider_requests=0,
        versions=dict(python=platform.python_version(), httpx=httpx.__version__,
            httpcore=importlib.metadata.version('httpcore')),
        sources={str(path.relative_to(ROOT)): sha256(path) for path in inputs})
    (args.output / 'source-freeze.json').write_text(json.dumps(freeze, indent=2)+'\n')
    results = run_probes()
    (args.output / 'raw-results.json').write_text(json.dumps(results, indent=2)+'\n')
    try:
        summary = validate(results)
    except AssertionError:
        summary = dict(status='failed', provider_requests=0, error='Local acceptance failed; inspect retained raw results.')
        raise
    finally:
        summary.update(finished_at=datetime.now(timezone.utc).isoformat(),
            source_changed_during_probe=[str(path.relative_to(ROOT)) for path in inputs
                if freeze['sources'][str(path.relative_to(ROOT))] != sha256(path)],
            results_sha256=sha256(args.output / 'raw-results.json'))
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
