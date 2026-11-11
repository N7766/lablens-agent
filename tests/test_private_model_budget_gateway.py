"""Budget enforcement against real loopback HTTP fixtures; no external inference."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('budget_gateway', ROOT / 'scripts/private_model_budget_gateway.py')
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)
CALLER = 'synthetic-local-gateway-secret'
UPSTREAM = 'synthetic-upstream-model-secret'


def payload(index):
    return dict(model='lablens-private', messages=[dict(role='user', content=f'synthetic {index}')],
                temperature=0, max_tokens=100, response_format=dict(type='json_schema', json_schema=dict(
                    name='workspace_action' if index < 3 else 'evidence_review_by_id', strict=True,
                    schema={'type': 'object'})))


@pytest.fixture
def upstream():
    state = dict(calls=[], behavior='normal', entered=threading.Event(), release=threading.Event(), closed=threading.Event())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append(dict(payload=data, authorization=self.headers.get('Authorization'), path=self.path))
            if state.get('assert_before_send'):
                state['assert_before_send']()
            state['entered'].set()
            kind = state['behavior']
            if kind == 'gated':
                state['release'].wait(2)
            try:
                if kind == 'trickle':
                    self.send_response(200)
                    self.send_header('Content-Length', '10000')
                    self.end_headers()
                    for _ in range(10000):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.015)
                    return
                status = 302 if kind == 'redirect' else 503 if kind == 'http_error' else 200
                body = gateway.canonical({'choices': [], 'synthetic': True})
                if kind == 'oversize':
                    body = b'x' * (gateway.MAX_BODY + 1)
                if kind == 'leak':
                    body = gateway.canonical({'content': UPSTREAM})
                if kind == 'nested_leak':
                    escaped = ''.join('\\u%04x' % ord(c) for c in UPSTREAM)
                    body = gateway.canonical({'content': '{"key":"' + escaped + '"}'})
                self.send_response(status)
                if kind == 'redirect':
                    self.send_header('Location', '/must-not-follow')
                if kind == 'gzip':
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Content-Length', str(len(body) + 20 if kind == 'disconnect' else len(body)))
                self.end_headers()
                self.wfile.write(body)
                if kind == 'disconnect':
                    self.close_connection = True
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state['closed'].set()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/v1'
    yield state
    state['release'].set()
    server.shutdown()
    server.server_close()
    thread.join(1)


def candidate(tmp_path, upstream, *, request_seconds=1, phase_seconds=10):
    # Reserve a free test port briefly; Server must still fail if another process wins later.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    probe_dir = tmp_path / 'probe'
    probe_dir.mkdir()
    requests = [dict(name=name, payload=payload(i), payload_sha256=gateway.digest(gateway.canonical(payload(i))))
                for i, name in enumerate(('chinese', 'workspace_action', 'scoped_review'), 1)]
    plan = dict(base_url=f'http://127.0.0.1:{port}/v1', model='lablens-private', max_requests=3, requests=requests)
    raw = gateway.canonical(plan)
    (probe_dir / 'plan.json').write_bytes(raw)
    (probe_dir / 'plan.sha256').write_text(gateway.digest(raw) + '\n')
    directory = tmp_path / 'gate'
    gateway.prepare(directory, compatibility_plan=probe_dir / 'plan.json', upstream=upstream['url'], listen_port=port,
                    upstream_mode='loopback_fixture', request_seconds=request_seconds, phase_seconds=phase_seconds)
    return directory, probe_dir


@contextmanager
def serving(directory, phase='compat', *, summary=None):
    session = gateway.Session(directory, phase, compatibility_summary=summary)
    server = gateway.GatewayServer(session, CALLER, UPSTREAM)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False, timeout=3,
                      headers={'Authorization': 'Bearer ' + CALLER}) as client:
        try:
            yield session, client
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)
            session.close()


def rows(directory, phase='compat'):
    return [json.loads(line) for line in (directory / f'{phase}.jsonl').read_text().splitlines()]


def receipt(directory, probe_dir):
    completed = [row for row in rows(directory) if row['event'] == 'completed']
    value = dict(status='compatibility_passed', dispatch_intents=3, planned_requests=3, skipped_requests=0,
                 frozen_plan_sha256=gateway.digest((probe_dir / 'plan.json').read_bytes()), attempts=[
                     dict(index=i, name=name, status='passed', outcome_unknown=False, body_complete=True,
                          status_code=200, response_redacted=False, sanitized_body_sha256=completed[i-1]['response_sha256'])
                     for i, name in enumerate(('chinese', 'workspace_action', 'scoped_review'), 1)])
    path = probe_dir / 'summary.json'
    path.write_text(json.dumps(value))
    return path


def complete_compat(directory, probe_dir):
    with serving(directory) as (_, client):
        for i in range(1, 4):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
    return receipt(directory, probe_dir)


def test_prepare_is_offline_and_exclusive(tmp_path, upstream):
    directory, probe_dir = candidate(tmp_path, upstream)
    assert upstream['calls'] == []
    assert oct(directory.stat().st_mode & 0o777) == '0o700'
    assert oct((directory / 'plan.json').stat().st_mode & 0o777) == '0o600'
    with pytest.raises(FileExistsError):
        gateway.prepare(directory, compatibility_plan=probe_dir / 'plan.json', upstream=upstream['url'],
                        listen_port=json.loads((directory / 'plan.json').read_text())['listen_port'])


def test_six_requests_exact_phase_order_durable_before_forward_and_safe_health(tmp_path, upstream, monkeypatch):
    directory, probe_dir = candidate(tmp_path, upstream)
    def durable():
        phase = 'agent' if (directory / 'agent.started.json').exists() else 'compat'
        assert rows(directory, phase)[-1]['event'] == 'reserved'
    upstream['assert_before_send'] = durable
    monkeypatch.setenv('HTTP_PROXY', 'http://wrong-user:wrong-secret@127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    summary = complete_compat(directory, probe_dir)
    with serving(directory, 'agent', summary=summary) as (_, client):
        before = client.get('/health').json()
        assert set(before) == {'phase', 'reserved', 'completed', 'blocked', 'plan_sha256', 'upstream_mode'}
        assert before['reserved'] == 0 and before['phase'] == 'agent'
        for i in range(1, 4):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
        assert client.get('/health').json()['completed'] == 3
    assert len(upstream['calls']) == 6
    assert all(row['authorization'] == 'Bearer ' + UPSTREAM for row in upstream['calls'])
    for file in directory.iterdir():
        if file.is_file():
            assert CALLER.encode() not in file.read_bytes() and UPSTREAM.encode() not in file.read_bytes()
    for phase in ('compat', 'agent'):
        with pytest.raises((FileExistsError, gateway.GatewayError)):
            gateway.Session(directory, phase, compatibility_summary=summary if phase == 'agent' else None)


def test_fourth_request_cannot_forward(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        for i in range(1, 4):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
        assert client.post('/v1/chat/completions', json=payload(3)).status_code == 409
        assert session.health()['reserved'] == 3 and session.health()['blocked']
    assert len(upstream['calls']) == 3


def test_inflight_race_has_one_reservation_then_no_refund(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    upstream['behavior'] = 'gated'
    with serving(directory) as (session, client), ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(client.post, '/v1/chat/completions', json=payload(1))
        assert upstream['entered'].wait(1)
        statuses = list(pool.map(lambda _: client.post('/v1/chat/completions', json=payload(1)).status_code, range(7)))
        assert statuses == [409] * 7
        assert session.reserved == 1
        upstream['release'].set()
        assert first.result().status_code == 200
    assert len(upstream['calls']) == 1


def test_last_slot_race_forwards_only_once(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        for i in (1, 2):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
        upstream['entered'].clear()
        upstream['behavior'] = 'gated'
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(client.post, '/v1/chat/completions', json=payload(3))
            assert upstream['entered'].wait(1)
            rest = [pool.submit(client.post, '/v1/chat/completions', json=payload(3)) for _ in range(3)]
            assert [job.result().status_code for job in rest] == [409, 409, 409]
            upstream['release'].set()
            assert first.result().status_code == 200
        assert session.reserved == 3
    assert len(upstream['calls']) == 3


def test_restart_after_durable_reservation_fails_closed(tmp_path, upstream):
    directory, probe_dir = candidate(tmp_path, upstream)
    session = gateway.Session(directory, 'compat')
    session.reserve(payload(1))
    session._unlock()  # Simulate process death: no close receipt or completion.
    with pytest.raises(FileExistsError):
        gateway.Session(directory, 'compat')
    with pytest.raises(gateway.GatewayError):
        gateway.Session(directory, 'agent', compatibility_summary=probe_dir / 'missing.json')
    assert len(rows(directory)) == 1 and not upstream['calls']


def test_parallel_process_phase_claim_is_locked(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    session = gateway.Session(directory, 'compat')
    try:
        with pytest.raises(BlockingIOError):
            gateway.Session(directory, 'compat')
    finally:
        session.close()


@pytest.mark.parametrize('behavior,error', [
    ('redirect', 'upstream_http_status'), ('http_error', 'upstream_http_status'),
    ('oversize', 'response_size_limit'), ('gzip', 'encoded_response_not_allowed'),
    ('disconnect', 'upstream_transport'), ('trickle', 'upstream_deadline'),
    ('leak', 'upstream_credential_leak_blocked'), ('nested_leak', 'upstream_credential_leak_blocked'),
])
def test_failure_consumes_quota_blocks_retry_and_does_not_leak(tmp_path, upstream, behavior, error):
    directory, _ = candidate(tmp_path, upstream, request_seconds=.15)
    upstream['behavior'] = behavior
    with serving(directory) as (session, client):
        started = time.monotonic()
        result = client.post('/v1/chat/completions', json=payload(1))
        assert result.status_code in (502, 504)
        assert result.json()['error'] == error
        assert time.monotonic() - started < 1.5
        assert UPSTREAM not in result.text and CALLER not in result.text
        assert session.health()['reserved'] == 1 and session.health()['completed'] == 0 and session.health()['blocked']
        assert client.post('/v1/chat/completions', json=payload(1)).status_code == 409
    assert len(upstream['calls']) == 1
    assert rows(directory)[-1]['event'] == 'failed'
    assert UPSTREAM not in (directory / 'compat.jsonl').read_text()


@pytest.mark.parametrize('mutation', ['plan', 'status', 'unknown', 'missing', 'hash', 'redacted'])
def test_agent_requires_exact_complete_success_receipt(tmp_path, upstream, mutation):
    directory, probe_dir = candidate(tmp_path, upstream)
    summary_path = complete_compat(directory, probe_dir)
    summary = json.loads(summary_path.read_text())
    if mutation == 'plan':
        summary['frozen_plan_sha256'] = '0' * 64
    elif mutation == 'status':
        summary['status'] = 'blocked'
    elif mutation == 'unknown':
        summary['attempts'][0]['outcome_unknown'] = True
    elif mutation == 'missing':
        summary['attempts'].pop()
    elif mutation == 'hash':
        summary['attempts'][0]['sanitized_body_sha256'] = '0' * 64
    else:
        summary['attempts'][0]['response_redacted'] = True
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(gateway.GatewayError):
        gateway.Session(directory, 'agent', compatibility_summary=summary_path)
    assert len(upstream['calls']) == 3


def test_agent_rejects_third_action_before_upstream(tmp_path, upstream):
    directory, probe_dir = candidate(tmp_path, upstream)
    summary = complete_compat(directory, probe_dir)
    with serving(directory, 'agent', summary=summary) as (session, client):
        for i in (1, 2):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
        wrong = payload(3)
        wrong['response_format']['json_schema']['name'] = 'workspace_action'
        assert client.post('/v1/chat/completions', json=wrong).status_code == 409
        assert session.reserved == 2 and session.blocked
    assert len(upstream['calls']) == 5


@pytest.mark.parametrize('mutation', ['model', 'stream', 'temperature', 'max_tokens', 'messages', 'strict', 'kind'])
def test_agent_rejects_unfrozen_payload_shapes(tmp_path, upstream, mutation):
    directory, probe_dir = candidate(tmp_path, upstream)
    summary = complete_compat(directory, probe_dir)
    wrong = payload(1)
    if mutation == 'model':
        wrong['model'] = 'other'
    elif mutation == 'stream':
        wrong['stream'] = True
    elif mutation == 'temperature':
        wrong['temperature'] = 1
    elif mutation == 'max_tokens':
        wrong['max_tokens'] = 401
    elif mutation == 'messages':
        wrong['messages'] = [{'role': 'user', 'content': 'test', 'image_url': 'http://invalid'}]
    elif mutation == 'strict':
        wrong['response_format']['json_schema']['strict'] = False
    else:
        wrong['response_format']['json_schema']['name'] = 'evidence_review_by_id'
    with serving(directory, 'agent', summary=summary) as (session, client):
        assert client.post('/v1/chat/completions', json=wrong).status_code == 409
        assert session.reserved == 0 and session.blocked
    assert len(upstream['calls']) == 3


def test_auth_paths_invalid_framing_and_duplicate_json_do_not_forward(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        assert client.get('/health', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.post('/v1/embeddings', json={}).status_code == 404
        assert client.post('/v1/chat/completions?key=private', json={}).status_code == 404
        assert client.post('/v1/chat/completions', content=b'{"model":"a","model":"b"}',
                           headers={'Content-Type': 'application/json'}).status_code == 400
        assert client.post('/v1/chat/completions', content=b'x' * (gateway.MAX_BODY + 1),
                           headers={'Content-Type': 'application/json'}).status_code == 400
        assert client.post('/v1/chat/completions', json={'content': UPSTREAM}).status_code == 400
        assert session.reserved == 0
    assert not upstream['calls']


@pytest.mark.parametrize('url', ['https://127.0.0.1:18080/v1', 'http://localhost:18080/v1',
    'http://127.0.0.1:8081/v1', 'http://127.0.0.1:8765/v1', 'http://example.com/v1',
    'http://user:secret@127.0.0.1:18080/v1', 'http://127.0.0.1:18080/v1?x=1',
    'http://127.0.0.1:18080/v1/', 'http://127.0.0.1:18080/v1#fragment'])
def test_upstream_is_exact_isolated_loopback(url):
    with pytest.raises(gateway.GatewayError):
        gateway.loopback_origin(url)


def test_plan_mutation_and_ledger_mutation_fail_closed(tmp_path, upstream):
    directory, probe_dir = candidate(tmp_path, upstream)
    summary = complete_compat(directory, probe_dir)
    with (directory / 'compat.jsonl').open('a') as stream:
        stream.write('{"event":"forged"}\n')
    with pytest.raises(gateway.GatewayError):
        gateway.Session(directory, 'agent', compatibility_summary=summary)
    value = json.loads((directory / 'plan.json').read_text())
    value['upstream'] = 'http://127.0.0.1:1/v1'
    (directory / 'plan.json').write_text(json.dumps(value))
    with pytest.raises(gateway.GatewayError):
        gateway.Session(directory, 'agent', compatibility_summary=summary)
    assert len(upstream['calls']) == 3


def test_reservation_fsync_failure_poisoned_without_upstream(tmp_path, upstream, monkeypatch):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        original = session.append
        def fail(_):
            raise OSError('private filesystem detail')
        monkeypatch.setattr(session, 'append', fail)
        response = client.post('/v1/chat/completions', json=payload(1))
        assert response.status_code == 503 and 'private filesystem' not in response.text
        assert session.blocked and session.reserved == 0
        monkeypatch.setattr(session, 'append', original)
        assert client.post('/v1/chat/completions', json=payload(1)).status_code == 409
    assert not upstream['calls']


def test_completion_fsync_failure_preserves_reserved_unknown(tmp_path, upstream, monkeypatch):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        original = session.append
        def fail_complete(row):
            if row['event'] == 'completed':
                raise OSError('secret must not surface')
            original(row)
        monkeypatch.setattr(session, 'append', fail_complete)
        response = client.post('/v1/chat/completions', json=payload(1))
        assert response.status_code == 503 and 'secret' not in response.text
        assert session.blocked and session.reserved == 1 and session.inflight
        assert rows(directory)[-1]['event'] == 'reserved'
    assert len(upstream['calls']) == 1


def test_phase_deadline_prevents_any_upstream(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream, phase_seconds=.02)
    with serving(directory) as (session, client):
        time.sleep(.03)
        assert client.post('/v1/chat/completions', json=payload(1)).status_code == 409
        assert session.reserved == 0 and session.blocked
    assert not upstream['calls']


def test_identical_keys_rejected_and_no_credential_output(tmp_path, upstream, capsys, monkeypatch):
    directory, _ = candidate(tmp_path, upstream)
    monkeypatch.setenv(gateway.CALLER_KEY_ENV, UPSTREAM)
    monkeypatch.setenv(gateway.UPSTREAM_KEY_ENV, UPSTREAM)
    assert gateway.main(['serve', '--directory', str(directory), '--phase', 'compat']) == 2
    output = capsys.readouterr()
    assert UPSTREAM not in output.out + output.err
    assert not (directory / 'compat.started.json').exists()


def test_symlink_evidence_rejected(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    original = directory / 'plan.json'
    target = tmp_path / 'copied.json'
    target.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(target)
    with pytest.raises(gateway.GatewayError):
        gateway.Session(directory, 'compat')


def test_fifo_evidence_rejected_without_blocking(tmp_path):
    path = tmp_path / 'fifo'
    os.mkfifo(path)
    started = time.monotonic()
    with pytest.raises(gateway.GatewayError):
        gateway.read_file(path)
    assert time.monotonic() - started < 1


def test_client_disconnect_never_refunds_or_resends(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    upstream['behavior'] = 'gated'
    with serving(directory) as (session, client):
        port = session.plan['listen_port']
        raw = gateway.canonical(payload(1))
        request = (f'POST /v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer {CALLER}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n').encode() + raw
        with socket.create_connection(('127.0.0.1', port), timeout=1) as conn:
            conn.sendall(request)
            assert upstream['entered'].wait(1)
        upstream['release'].set()
        until = time.monotonic() + 1
        while session.inflight and time.monotonic() < until:
            time.sleep(.01)
        assert session.reserved == session.completed == 1
        # Replaying the first payload is not the next frozen slot.
        assert client.post('/v1/chat/completions', json=payload(1)).status_code == 409
    assert len(upstream['calls']) == 1


def test_interrupted_phase_with_clean_close_still_cannot_restart(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    with serving(directory):
        pass
    with pytest.raises(FileExistsError):
        gateway.Session(directory, 'compat')
    assert not upstream['calls']


def test_stopping_fences_new_reservations_without_poisoning_completed_compatibility(tmp_path, upstream):
    directory, probe_dir = candidate(tmp_path, upstream)
    with serving(directory) as (session, client):
        for i in range(1, 4):
            assert client.post('/v1/chat/completions', json=payload(i)).status_code == 200
        session.stop_accepting()
        assert client.post('/v1/chat/completions', json=payload(3)).status_code == 409
        assert not session.blocked and session.reserved == session.completed == 3
    summary = receipt(directory, probe_dir)
    with serving(directory, 'agent', summary=summary) as (session, _):
        assert not session.stopping and session.reserved == 0
    assert len(upstream['calls']) == 3


def test_inflight_completion_does_not_reopen_stopped_admission(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    upstream['behavior'] = 'gated'
    with serving(directory) as (session, client), ThreadPoolExecutor(max_workers=1) as pool:
        started = pool.submit(client.post, '/v1/chat/completions', json=payload(1))
        assert upstream['entered'].wait(1)
        session.stop_accepting()
        upstream['release'].set()
        assert started.result().status_code == 200
        assert session.stopping and not session.inflight and not session.blocked
        assert client.post('/v1/chat/completions', json=payload(2)).status_code == 409
        assert session.reserved == session.completed == 1
    assert len(upstream['calls']) == 1


def test_sigterm_during_partial_body_does_not_start_upstream_after_stop(tmp_path, upstream):
    directory, _ = candidate(tmp_path, upstream)
    port = json.loads((directory / 'plan.json').read_text())['listen_port']
    env = {**os.environ, gateway.CALLER_KEY_ENV: CALLER, gateway.UPSTREAM_KEY_ENV: UPSTREAM}
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/private_model_budget_gateway.py'),
                                'serve', '--directory', str(directory), '--phase', 'compat'],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        with httpx.Client(trust_env=False, timeout=.2, headers={'Authorization': 'Bearer ' + CALLER}) as client:
            while True:
                try:
                    if client.get(f'http://127.0.0.1:{port}/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(.01)
        raw = gateway.canonical(payload(1))
        headers = (f'POST /v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer {CALLER}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n').encode()
        with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
            connection.sendall(headers + raw[:1])
            # Let the real accepted handler block on the remainder of its body.
            time.sleep(.08)
            assert upstream['calls'] == []
            process.send_signal(signal.SIGTERM)
            time.sleep(.2)
            # server_close joins the existing handler; it has not released the ledger.
            assert process.poll() is None
            connection.sendall(raw[1:])
            response = b''
            while True:
                chunk = connection.recv(8192)
                if not chunk:
                    break
                response += chunk
        output, errors = process.communicate(timeout=3)
        assert process.returncode == 0
        assert b'409' in response.split(b'\r\n', 1)[0]
        assert upstream['calls'] == []
        closed = json.loads((directory / 'compat.closed.json').read_text())
        assert closed['reserved'] == closed['completed'] == 0 and closed['inflight'] is False
        assert rows(directory) == []
        assert CALLER.encode() not in output + errors and UPSTREAM.encode() not in output + errors
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
