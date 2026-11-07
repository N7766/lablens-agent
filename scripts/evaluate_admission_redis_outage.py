#!/usr/bin/env python3
"""Real stop/restart acceptance for the isolated, unauthenticated Redis on 6383.

Uses only loopback HTTP and a controlled zero-provider worker. Never stops Redis
6380, accepts remote targets, retries POSTs, or overwrites previous evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 6383
ACTIVE = ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'} or parsed.port is None):
        raise ValueError('An explicit loopback HTTP origin is required')
    return value.rstrip('/')


def replay_input(path, endpoint):
    """Recover exactly one accepted request; never construct a guessed replay."""
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('method') != 'POST' or row.get('http_status') != 202:
                continue
            url = urlsplit(row.get('url', ''))
            match = re.fullmatch(r'/api/workspaces/([0-9a-f-]+)/tasks', url.path)
            if not match or origin(f'{url.scheme}://{url.netloc}') != endpoint:
                continue
            workspace = str(UUID(match[1]))
            body, response, key = row.get('request_body'), row.get('body'), row.get('idempotency_key')
            if (not isinstance(body, dict) or body.get('strategy') != 'bm25' or body.get('mode') != 'extractive'
                    or not isinstance(body.get('question'), str) or not body['question'].strip()
                    or not isinstance(response, dict) or not isinstance(key, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', key)):
                raise ValueError('Accepted observation is not an offline idempotent fixture')
            return dict(workspace_id=workspace, task_id=str(UUID(response['id'])), body=body, key=key)
    raise ValueError('No accepted task from the exact target origin was found')


def validate_config(config, pid_file, binary):
    config, pid_file, binary = config.resolve(strict=True), pid_file.resolve(strict=True), binary.resolve(strict=True)
    if binary.name != 'redis-server' or not os.access(binary, os.X_OK):
        raise ValueError('An executable redis-server binary is required')
    values = {}
    for line in config.read_text().splitlines():
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        name, *rest = fields
        name = name.lower()
        if name in {'include', 'requirepass', 'aclfile', 'user'}:
            raise ValueError('Only a self-contained unauthenticated experiment configuration is supported')
        values[name] = rest
    if values.get('port') != [str(PORT)]:
        raise ValueError('This experiment can only stop the isolated Redis port 6383')
    if not values.get('bind') or any(host not in {'127.0.0.1', '::1', '-::1'} for host in values['bind']):
        raise ValueError('Redis must bind only loopback addresses')
    if values.get('pidfile') != [str(pid_file)]:
        raise ValueError('Redis configuration must name the exact supplied PID file')
    return config, pid_file, binary


class RedisFixture:
    """PID + live INFO + listener identity, with no credentials or shell commands."""
    def __init__(self, config, pid_file, binary, emit, timeout=15):
        self.config, self.pid_file, self.binary = validate_config(config, pid_file, binary)
        self.emit, self.timeout, self.before_pid = emit, timeout, None
        self.stop_signalled = False

    @staticmethod
    def listening():
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=.3):
                return True
        except OSError:
            return False

    @staticmethod
    def running(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def info():
        with socket.create_connection(('127.0.0.1', PORT), timeout=1) as connection:
            connection.sendall(b'*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n')
            with connection.makefile('rb') as stream:
                header = stream.readline(128)
                if not re.fullmatch(rb'\$[0-9]+\r\n', header):
                    raise ValueError('Expected unauthenticated Redis INFO bulk response')
                length = int(header[1:-2])
                if not 1 <= length <= 65536:
                    raise ValueError('Redis INFO response exceeds diagnostic bound')
                body = stream.read(length)
                if len(body) != length or stream.read(2) != b'\r\n':
                    raise ValueError('Incomplete Redis INFO response')
        return dict(line.split(':', 1) for line in body.decode().splitlines() if ':' in line and not line.startswith('#'))

    def identity(self):
        pid = int(self.pid_file.read_text().strip())
        if pid <= 1:
            raise ValueError('Invalid Redis PID')
        process = subprocess.run(['ps', '-p', str(pid), '-o', 'comm=', '-o', 'args='],
                                 capture_output=True, text=True, timeout=3, check=True).stdout
        if 'redis-server' not in process:
            raise ValueError('PID is not a redis-server process')
        info = self.info()
        if (int(info.get('process_id', '0')) != pid or int(info.get('tcp_port', '0')) != PORT
                or Path(info.get('config_file', '/')).resolve() != self.config):
            raise ValueError('Live Redis identity does not match the isolated PID, port and configuration')
        # INFO is read over exactly 127.0.0.1:6383, proving which process serves that listener.
        identity = dict(pid=pid, port=PORT, config_file=str(self.config), redis_version=info.get('redis_version'),
                        ps_redis_server=True, configuration_in_ps=str(self.config) in process,
                        identity_method='pidfile+ps+loopback_INFO_server')
        self.emit('identity_verified', **identity)
        return identity

    def validate(self):
        identity = self.identity()
        self.before_pid = identity['pid']
        return identity

    def stop(self):
        identity = self.identity()  # Recheck immediately before signalling; never trust a stale PID file alone.
        if identity['pid'] != self.before_pid:
            raise ValueError('Redis PID changed after preflight')
        self.emit('stop_requested', pid=identity['pid'], signal='SIGTERM', port=PORT)
        self.stop_signalled = True
        os.kill(identity['pid'], signal.SIGTERM)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if not self.listening() and not self.running(identity['pid']):
                self.emit('process_exited_and_port_closed', old_pid=identity['pid'], port=PORT)
                return
            time.sleep(.1)
        raise RuntimeError('Redis did not close the experiment port within the stop allowance')

    def restore(self):
        deadline = time.monotonic() + self.timeout
        if self.stop_signalled:
            while self.running(self.before_pid) and time.monotonic() < deadline:
                time.sleep(.1)
            if self.running(self.before_pid):
                raise RuntimeError('Signalled Redis process has not exited; cannot safely launch a second process')
        if self.listening():
            identity = self.identity()
            self.emit('restore_already_listening', pid=identity['pid'])
            return identity
        self.emit('restart_requested', binary=str(self.binary), config_file=str(self.config), port=PORT)
        process = subprocess.Popen([str(self.binary), str(self.config)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.emit('launcher_started', pid=process.pid)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.listening():
                try:
                    identity = self.identity()
                    self.emit('restored', pid=identity['pid'], previous_pid=self.before_pid,
                              pid_changed=identity['pid'] != self.before_pid)
                    return identity
                except (ValueError, OSError, subprocess.SubprocessError):
                    pass  # The daemon may listen just before its PID file is replaced.
            if process.poll() not in (None, 0):
                raise RuntimeError('Redis restart process failed')
            time.sleep(.1)
        raise RuntimeError('Redis restoration was not verified within the restart allowance')


class Evidence:
    def __init__(self, directory):
        self.http = (directory / 'observations.jsonl').open('x', encoding='utf-8', buffering=1)
        self.process = (directory / 'process-events.jsonl').open('x', encoding='utf-8', buffering=1)
        self.sequence = 0

    def emit(self, event, **fields):
        self.process.write(json.dumps(dict(event=event, observed_at_utc=now(), **fields)) + '\n')

    def request(self, client, method, url, body=None, key=None):
        started = time.monotonic()
        row = dict(method=method, url=url, request_body=body, idempotency_key=key, observed_at_utc=now())
        try:
            response = client.request(method, url, json=body, headers={'Idempotency-Key': key} if key else None)
            try:
                payload = response.json() if response.content else None
            except ValueError:
                payload = {'invalid_json': True}
            row.update(http_status=response.status_code, body=payload, retry_after=response.headers.get('Retry-After'))
        except httpx.HTTPError as error:
            row.update(http_status=None, body=None, transport_error_type=type(error).__name__)
        self.sequence += 1
        row.update(sequence=self.sequence, elapsed_ms=round((time.monotonic()-started)*1000, 3))
        self.http.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def close(self):
        self.http.close()
        self.process.close()


def counts(status):
    value = status.get('task_counts')
    if not isinstance(value, dict) or not value or any(type(n) is not int or n < 0 for n in value.values()):
        raise ValueError('Persisted task counters are required')
    return sum(value.values()), sum(value.get(key, 0) for key in ACTIVE)


def run(args, *, client=None, redis=None):
    endpoint, worker_endpoint = origin(args.endpoint), origin(args.worker_endpoint)
    replay = replay_input(args.observations, endpoint)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError('Previous evidence cannot be overwritten')
    evidence = Evidence(args.output)
    summary = dict(completed=False, passed=False, restoration_required=False, restoration_verified=False,
                   started_at_utc=now(), checks=[], scope='Local isolated Redis fault with controlled zero-provider HTTP worker')
    dump(args.output / 'manifest.json', dict(created_at_utc=now(), endpoint=endpoint, worker_endpoint=worker_endpoint,
        redis_port=PORT, redis_config=str(args.redis_config.resolve()), redis_pid_file=str(args.redis_pid_file.resolve()),
        redis_binary=str(args.redis_binary.resolve()), source_observations_sha256=sha(args.observations),
        script_sha256=sha(Path(__file__)), replay=replay, task_timeout_seconds=args.task_timeout,
        stop_timeout_seconds=args.stop_timeout, model_calls_allowed=0,
        limitations=['Synthetic fixture, not production traffic or provider capacity',
                     'A real Redis process is stopped; MySQL and JVM stay running',
                     'POST transport failure is acceptance-unknown and is not retried']))
    owned_client = client is None
    client = client or httpx.Client(timeout=args.http_timeout, trust_env=False, follow_redirects=False)

    def check(name, passed, detail=None):
        summary['checks'].append(dict(name=name, passed=bool(passed), evidence=detail))
        if not passed:
            raise AssertionError(name)

    def request(method, path, body=None, key=None, *, worker=False):
        return evidence.request(client, method, (worker_endpoint if worker else endpoint)+path, body, key)

    def require_get(path, *, worker=False):
        row = request('GET', path, worker=worker)
        check('GET succeeds: '+path, row['http_status'] == 200)
        return row['body']

    def snapshot(name):
        system = require_get('/api/system/status')
        worker = require_get('/control/status', worker=True)
        value = dict(system=system, worker=worker)
        summary[name] = value
        return value

    def wait_redis(available):
        deadline = time.monotonic() + args.task_timeout
        while True:
            status = require_get('/api/system/status')
            if status.get('redis', {}).get('available') is available:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError('Redis availability did not reach expected state')
            time.sleep(.1)

    def complete_offline(label):
        body = dict(replay['body'], question=f'Find admission_sentinel evidence for Redis outage {label} {uuid4()}')
        row = request('POST', f'/api/workspaces/{replay["workspace_id"]}/tasks', body, 'redis-outage-'+str(uuid4()))
        check(label+' fresh offline task accepted', row['http_status'] == 202 and not row['body'].get('reused', False))
        task_id = str(UUID(row['body']['id']))
        deadline = time.monotonic() + args.task_timeout
        while True:
            task = require_get(f'/api/workspaces/{replay["workspace_id"]}/tasks/{task_id}')
            if task.get('status') in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Accepted task did not reach a terminal state')
            time.sleep(.1)
        usage = task.get('execution_usage') or (task.get('result') or {}).get('usage') or {}
        check(label+' offline task completes without provider or cache', task.get('status') == 'SUCCEEDED'
              and task.get('cache_hit') is False and usage.get('provider_usage_unknown') is False
              and all(usage.get(key) == 0 for key in ('model_calls', 'embedding_requests', 'embedded_inputs')))
        summary[label+'_task'] = task

    phase = 'preflight'
    try:
        before = snapshot('before')
        total, active = counts(before['system'])
        wb = before['worker']
        check('Dedicated system is drained and admission enabled', active == 0
              and before['system'].get('database') == 'available'
              and before['system'].get('redis', {}).get('available') is True
              and before['system'].get('redis', {}).get('rate_limit_enabled') is True)
        check('Controlled zero-provider worker is drained', wb.get('fixture') == 'synthetic-bm25-extractive-only'
              and wb.get('worker_sha256') == sha(ROOT/'scripts/controlled_admission_worker.py')
              and wb.get('provider_calls') == 0 and wb.get('active') == 0 and wb.get('failed') == 0
              and wb.get('started') == wb.get('completed'))
        original = require_get(f'/api/workspaces/{replay["workspace_id"]}/tasks/{replay["task_id"]}')
        check('Replay source task has completed', original.get('status') == 'SUCCEEDED' and original.get('id') == replay['task_id'])
        documents = require_get(f'/api/workspaces/{replay["workspace_id"]}/documents')
        revision = documents.get('revision')
        check('Current document approval revision is known', type(revision) is int and revision >= 0 and bool(documents.get('documents')))
        redis = redis or RedisFixture(args.redis_config, args.redis_pid_file, args.redis_binary, evidence.emit, args.stop_timeout)
        summary['redis_before'] = redis.validate()
        try:
            summary['restoration_required'] = True
            phase = 'redis_outage'
            redis.stop()
            wait_redis(False)
            provider = dict(question='Find admission_sentinel evidence', strategy='bm25', mode='agent', top_k=1,
                            approved_revision=revision)
            rejected = request('POST', f'/api/workspaces/{replay["workspace_id"]}/tasks', provider, 'redis-provider-'+str(uuid4()))
            check('Provider task is retryable 503 admission_unavailable', rejected['http_status'] == 503
                  and rejected['body'].get('error') == 'admission_unavailable' and rejected['body'].get('retryable') is True
                  and rejected['body'].get('retry_after_seconds') == 1 and rejected['retry_after'] == '1')
            after_provider = snapshot('after_provider_rejection')
            check('Rejected provider work creates no task and never reaches worker', counts(after_provider['system']) == (total, 0)
                  and all(after_provider['worker'].get(key) == wb.get(key) for key in ('started', 'completed', 'failed', 'active', 'provider_calls')))
            repeated = request('POST', f'/api/workspaces/{replay["workspace_id"]}/tasks', replay['body'], replay['key'])
            check('Durable replay is 200 with same task during outage', repeated['http_status'] == 200
                  and repeated['body'].get('id') == replay['task_id'] and repeated['body'].get('reused') is True)
            after_replay = snapshot('after_replay')
            check('Replay creates no task and no execution', counts(after_replay['system']) == (total, 0)
                  and all(after_replay['worker'].get(key) == wb.get(key) for key in ('started', 'completed', 'failed', 'active', 'provider_calls')))
            complete_offline('during_outage')
            during = snapshot('during_outage')
            check('Offline work completes exactly once while Redis is down', counts(during['system']) == (total+1, 0)
                  and during['system'].get('redis', {}).get('available') is False
                  and during['worker'].get('started') == wb['started']+1 and during['worker'].get('completed') == wb['completed']+1
                  and during['worker'].get('failed') == 0 and during['worker'].get('provider_calls') == 0)
        except BaseException as error:
            summary['outage_failure'] = dict(error_type=type(error).__name__, phase=phase)
            raise
        finally:
            # Covers assertion failures, HTTP failures, interrupt and partial stop failures.
            try:
                summary['redis_after'] = redis.restore()
                summary['restoration_verified'] = True
            except BaseException as error:
                summary['restoration_failure'] = dict(error_type=type(error).__name__, phase='restore')
                raise
        phase = 'after_restore'
        wait_redis(True)
        complete_offline('after_restore')
        after = snapshot('after')
        check('Recovered Redis allows one additional zero-provider task', counts(after['system']) == (total+2, 0)
              and after['system'].get('redis', {}).get('available') is True
              and after['worker'].get('started') == wb['started']+2 and after['worker'].get('completed') == wb['completed']+2
              and after['worker'].get('failed') == 0 and after['worker'].get('provider_calls') == 0)
        summary.update(completed=True, passed=True)
    except BaseException as error:
        summary['failure'] = dict(phase=phase, error_type=type(error).__name__,
                                  message='Acceptance stopped; inspect recorded HTTP, process events and failed checks. No POST retry.')
        if isinstance(error, KeyboardInterrupt):
            summary['interrupted'] = True
    finally:
        summary['finished_at_utc'] = now()
        dump(args.output/'summary.json', summary)
        evidence.close()
        if owned_client:
            client.close()
        dump(args.output/'raw-seal.json', {'files': {p.name: sha(p) for p in sorted(args.output.iterdir()) if p.is_file() and p.name != 'raw-seal.json'}})
    return 0 if summary['passed'] else (130 if summary.get('interrupted') else 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8086')
    parser.add_argument('--worker-endpoint', default='http://127.0.0.1:8770')
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--redis-config', type=Path, required=True)
    parser.add_argument('--redis-pid-file', type=Path, required=True)
    parser.add_argument('--redis-binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-timeout', type=float, default=30)
    parser.add_argument('--stop-timeout', type=float, default=15)
    parser.add_argument('--http-timeout', type=float, default=5)
    args = parser.parse_args()
    if not (.1 <= args.http_timeout <= 10 and 1 <= args.stop_timeout <= 30 and 1 <= args.task_timeout <= 60):
        parser.error('Timeout bounds: HTTP .1..10, stop 1..30, task 1..60 seconds')
    try:
        code = run(args)
        print(json.dumps(dict(exit_code=code, output=str(args.output))))
        return code
    except Exception as error:
        print(json.dumps(dict(exit_code=2, error_type=type(error).__name__,
                             message='Evaluation could not finish; inspect retained evidence and verify isolated Redis availability.')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
