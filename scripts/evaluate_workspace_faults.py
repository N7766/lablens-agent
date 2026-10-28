#!/usr/bin/env python3
"""Actual JVM/MySQL/Redis process-fault controls with an explicitly synthetic worker.

Never targets the regular workspace service. Creates a new database, starts owned
processes on dedicated loopback ports, records evidence, and stops only its PIDs.
This validates durability/failure contracts, not RAG quality or model performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ControlledWorker:
    def __init__(self, port):
        self.events = []
        self.lock = threading.Lock()
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                question = payload['question']
                event = {'question': question, 'received_at': timestamp(),
                         'snapshot_id': payload['snapshot_id'], 'mode': payload['mode'],
                         'strategy': payload['strategy']}
                with worker.lock:
                    worker.events.append(event)
                usage = {'model_calls': 0, 'embedding_requests': 0, 'embedded_inputs': 0,
                         'embedding_cache_hits': 0, 'embedding_tokens': 0,
                         'generation_tokens': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                         'provider_usage_unknown': False, 'budget_exhausted': False}
                result = {'status': 'excerpts', 'answer': 'Synthetic process-control response; not RAG inference.',
                          'mode': payload['mode'], 'citations': [], 'claims': [], 'usage': usage,
                          'retrieval': {'snapshot_id': payload['snapshot_id'], 'strategy': payload['strategy'],
                                        'documents_sha256': hashlib.sha256(json.dumps(payload['documents'], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
                                        'hits': [], 'latency_ms': 0}, 'latency_ms': 0,
                          'limitations': ['Controlled worker; no actual retrieval, embedding or LLM execution.']}
                status, body = 200, result
                if question.startswith('FAULT:slow'):
                    time.sleep(5)
                elif question.startswith('FAULT:malformed'):
                    body = {'unexpected': 'JSON object without the worker contract'}
                elif question.startswith('FAULT:unavailable'):
                    status = 503
                    result.update(status='unavailable', answer='', error='Controlled unavailable configuration; zero provider calls.')
                    body = {'detail': result}
                elif question.startswith('FAULT:provider-unknown'):
                    status = 502
                    usage.update(embedding_requests=1, embedded_inputs=1, embedding_tokens=None, provider_usage_unknown=True)
                    result.update(status='provider_error', answer='', error='Controlled provider timeout; usage unknown.')
                    body = {'detail': result}
                encoded = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    event['response_written'] = True
                except (BrokenPipeError, ConnectionResetError):
                    event['response_written'] = False
                event['response_status'] = status
                event['finished_at'] = timestamp()

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, question):
        with self.lock:
            return sum(event['question'] == question for event in self.events)


class Experiment:
    def __init__(self, args):
        self.args = args
        self.run_id = uuid.uuid4().hex[:8]
        self.database = 'lablens_faults_' + self.run_id
        self.private = ROOT / '.local' / 'faults' / self.run_id
        self.private.mkdir(parents=True)
        self.output = Path(args.output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.base = f'http://127.0.0.1:{args.jvm_port}'
        self.http = httpx.Client(trust_env=False, timeout=10)
        self.env = {**os.environ, **{key: value for key, value in dotenv_values(ROOT / '.local/services.env').items() if value is not None}}
        self.jvm = self.redis = self.worker = None
        self.files = []
        self.jvm_number = self.redis_number = 0
        self.summary = {'run_id': self.run_id, 'started_at': timestamp(), 'status': 'running',
                        'scope': 'Real JVM process restarts/SIGKILL, real MySQL durability, real Redis outages; controlled HTTP worker only.',
                        'real_model_calls': 0, 'real_embedding_requests': 0,
                        'database': self.database, 'ports': {'jvm': args.jvm_port, 'worker': args.worker_port, 'redis': args.redis_port},
                        'configuration': {'workers': 1, 'lease_seconds': 3, 'worker_timeout_seconds': 2,
                                          'submission_limit_per_minute': 2},
                        'cases': [], 'process_events': [],
                        'limitations': ['No model inference or RAG ranking runs in this experiment.',
                                        'Single-host local process controls do not establish distributed or production-scale availability.',
                                        'A killed/timeout request may continue at an external provider; automatic retry is deliberately disabled.']}

    def record(self, name, checks, evidence):
        passed = all(checks.values())
        self.summary['cases'].append({'name': name, 'passed': passed, 'checks': checks, 'evidence': evidence, 'recorded_at': timestamp()})
        self.save()
        print(json.dumps({'case': name, 'passed': passed}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(f'{name}: ' + ', '.join(key for key, value in checks.items() if not value))

    def save(self):
        (self.output / 'summary.json').write_text(json.dumps(self.summary, ensure_ascii=False, indent=2) + '\n')
        if self.worker:
            (self.output / 'worker-events.json').write_text(json.dumps(self.worker.events, ensure_ascii=False, indent=2) + '\n')

    def request(self, method, path, expected=200, **kwargs):
        response = self.http.request(method, self.base + path, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f'{method} {path}: expected {expected}, received {response.status_code}: {response.text[:600]}')
        return response.json() if response.content else None

    def wait(self, predicate, seconds=20):
        end = time.monotonic() + seconds
        last = None
        while time.monotonic() < end:
            try:
                last = predicate()
                if last:
                    return last
            except (httpx.HTTPError, AssertionError):
                pass
            time.sleep(.1)
        raise AssertionError(f'Condition did not become true in {seconds} seconds; last={last}')

    def prepare(self):
        if not self.args.jar.is_file():
            raise RuntimeError('Build the workspace jar before running fault controls')
        for port in (self.args.jvm_port, self.args.worker_port, self.args.redis_port):
            if port in (8081, 8765, 6380):
                raise RuntimeError('Dedicated ports must not overlap the main workbench')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        self.summary['versions'] = {
            'java': subprocess.run([self.args.java, '-version'], capture_output=True, text=True, check=True).stderr.strip(),
            'redis': subprocess.check_output([self.args.redis_binary, '--version'], text=True).strip(),
            'mysql': subprocess.check_output([self.args.mysql, '--version'], text=True).strip(),
            'python': sys.version,
        }
        inventory = [self.args.jar, Path(__file__).resolve()]
        inventory += sorted((ROOT / 'services/workspace/src/main').rglob('*.java'))
        inventory += sorted((ROOT / 'services/workspace/src/main').rglob('*.sql'))
        inventory += [ROOT / 'services/workspace/src/main/resources/application.properties']
        self.summary['source_sha256'] = {str(path.relative_to(ROOT)): digest(path) for path in inventory}
        sql = f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4; GRANT ALL ON `{self.database}`.* TO 'lablens'@'127.0.0.1';"
        subprocess.run([self.args.mysql, '--no-defaults', f'--socket={self.args.mysql_socket}', '-uroot'],
                       input=sql, text=True, capture_output=True, check=True)
        old_url = self.env['LABLENS_DB_URL']
        new_url = re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+', r'\g<1>' + self.database, old_url)
        if new_url == old_url or self.database not in new_url:
            raise RuntimeError('Could not bind a dedicated database')
        self.env.update(LABLENS_DB_URL=new_url, LABLENS_WORKSPACE_PORT=str(self.args.jvm_port),
                        LABLENS_WORKSPACE_BIND='127.0.0.1', LABLENS_REDIS_HOST='127.0.0.1',
                        LABLENS_REDIS_PORT=str(self.args.redis_port),
                        LABLENS_WORKER_BASE_URL=f'http://127.0.0.1:{self.args.worker_port}',
                        LABLENS_WORKERS='1', LABLENS_TASK_LEASE_SECONDS='3', LABLENS_WORKER_TIMEOUT_SECONDS='2',
                        LABLENS_RATE_LIMIT='2', LABLENS_CACHE_NAMESPACE='faults-' + self.run_id,
                        LABLENS_MAX_PENDING_TASKS='20')
        self.worker = ControlledWorker(self.args.worker_port)
        self.worker.start()
        self.start_redis()

    def start_redis(self):
        self.redis_number += 1
        log = (self.private / f'redis-{self.redis_number}.log').open('w')
        self.files.append(log)
        self.redis = subprocess.Popen([self.args.redis_binary, '--bind', '127.0.0.1', '--port', str(self.args.redis_port),
                                       '--dir', str(self.private), '--save', '', '--appendonly', 'no'], stdout=log, stderr=subprocess.STDOUT)
        self.summary['process_events'].append({'event': 'redis_start', 'pid': self.redis.pid, 'at': timestamp()})
        def ready():
            with socket.create_connection(('127.0.0.1', self.args.redis_port), timeout=.2) as sock:
                sock.sendall(b'*1\r\n$4\r\nPING\r\n')
                return sock.recv(16) == b'+PONG\r\n'
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                if ready():
                    return
            except OSError:
                time.sleep(.1)
        raise AssertionError('Owned Redis failed to start')

    def stop_redis(self):
        self.stop_process(self.redis, 'redis')
        self.redis = None

    def start_jvm(self, dispatch=True):
        self.jvm_number += 1
        log = (self.private / f'jvm-{self.jvm_number}.log').open('w')
        self.files.append(log)
        self.jvm = subprocess.Popen([self.args.java, '-jar', str(self.args.jar)],
                                    env={**self.env, 'LABLENS_DISPATCH_ENABLED': str(dispatch).lower()},
                                    stdout=log, stderr=subprocess.STDOUT)
        pid = self.jvm.pid
        self.summary['process_events'].append({'event': 'jvm_start', 'pid': pid, 'dispatch': dispatch, 'at': timestamp()})
        def ready():
            if self.jvm.poll() is not None:
                raise RuntimeError(f'Owned JVM {pid} exited; inspect private log {log.name}')
            response = self.http.get(self.base + '/api/system/status')
            body = response.json()
            return body if response.status_code == 200 and body.get('database') == 'available' else None
        return self.wait(ready, 50)

    def stop_process(self, process, name, kill=False):
        if process is None or process.poll() is not None:
            return
        if kill:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.summary['process_events'].append({'event': name + ('_sigkill' if kill else '_stop'), 'pid': process.pid,
                                               'exit_code': process.returncode, 'at': timestamp()})

    def stop_jvm(self, kill=False):
        self.stop_process(self.jvm, 'jvm', kill)
        self.jvm = None

    def workspace(self, name):
        item = self.request('POST', '/api/workspaces', expected=201, json={'name': name})
        wid = item['id']
        self.request('PUT', f'/api/workspaces/{wid}/documents/control', json={
            'title': 'Synthetic durability control', 'text': 'A deterministic fixture for process failure checks.',
            'source': 'synthetic:process-fault-control'})
        return wid

    def submit(self, wid, question, key=None, strategy='bm25', mode='extractive', expected=202):
        payload = {'question': question, 'strategy': strategy, 'mode': mode, 'top_k': 1}
        if mode != 'extractive' or strategy != 'bm25':
            payload['approved_revision'] = self.request('GET', f'/api/workspaces/{wid}/documents')['revision']
        return self.request('POST', f'/api/workspaces/{wid}/tasks', expected=expected,
                            headers={'Idempotency-Key': key or uuid.uuid4().hex}, json=payload)

    def task(self, wid, task):
        return self.request('GET', f'/api/workspaces/{wid}/tasks/{task["id"]}')

    def terminal(self, wid, task):
        return self.wait(lambda: (value if (value := self.task(wid, task))['status'] in ('SUCCEEDED', 'FAILED') else None))

    def run_cases(self):
        self.start_jvm(dispatch=False)
        wid = self.workspace('queued-survives-restart')
        queued = self.submit(wid, 'CONTROL:queued-restart')
        before = self.task(wid, queued)
        old_pid = self.jvm.pid
        self.stop_jvm()
        self.start_jvm()
        final = self.terminal(wid, queued)
        self.record('queued_survives_actual_jvm_restart', {
            'queued_before_shutdown': before['status'] == 'QUEUED', 'same_task_id': before['id'] == final['id'],
            'succeeded_after_restart': final['status'] == 'SUCCEEDED', 'different_jvm_pid': old_pid != self.jvm.pid,
            'one_worker_request': self.worker.count('CONTROL:queued-restart') == 1}, {'before': before, 'after': final})

        wid = self.workspace('running-crash-and-queued-recovery')
        crashed = self.submit(wid, 'FAULT:slow-crash', strategy='hybrid')
        self.wait(lambda: self.worker.count('FAULT:slow-crash') == 1)
        running = self.task(wid, crashed)
        surviving = self.submit(wid, 'CONTROL:after-crash')
        queued_before = self.task(wid, surviving)
        self.stop_jvm(kill=True)
        self.start_jvm()
        failed = self.terminal(wid, crashed)
        survived = self.terminal(wid, surviving)
        time.sleep(1)
        self.record('sigkill_running_fails_without_retry_queued_survives', {
            'running_before_sigkill': running['status'] == 'RUNNING',
            'other_task_queued': queued_before['status'] == 'QUEUED',
            'expired_running_failed': failed['status'] == 'FAILED' and failed['error_code'] == 'worker_interrupted',
            'hybrid_extractive_usage_unknown': failed['usage_unknown'] is True,
            'no_automatic_retry_observed': self.worker.count('FAULT:slow-crash') == 1,
            'queued_task_succeeded': survived['status'] == 'SUCCEEDED'},
            {'running_before': running, 'queued_before': queued_before, 'failed_after_restart': failed,
             'queued_after_restart': survived, 'no_retry_observation': 'Through JVM restart, lease expiry, queued completion and an additional 1 second.'})

        for label, question, strategy, expected_code, expected_unknown in [
            ('malformed_worker_json', 'FAULT:malformed', 'bm25', 'worker_invalid_result', False),
            ('worker_unavailable_zero_provider_calls', 'FAULT:unavailable', 'hybrid', 'rag_unavailable', False),
            ('worker_reports_unknown_embedding_usage', 'FAULT:provider-unknown', 'hybrid', 'provider_failed', True),
            ('worker_http_timeout_embedding_usage_unknown', 'FAULT:slow-timeout', 'hybrid', 'worker_timeout', True),
        ]:
            wid = self.workspace(label)
            submitted = self.submit(wid, question, strategy=strategy)
            final = self.terminal(wid, submitted)
            self.record(label, {'task_failed': final['status'] == 'FAILED',
                                'failure_code': final['error_code'] == expected_code,
                                'usage_uncertainty_correct': final['usage_unknown'] is expected_unknown,
                                'one_worker_attempt': self.worker.count(question) == 1}, {'task': final})

        wid = self.workspace('redis-outage-dedup')
        self.stop_redis()
        degraded = self.request('GET', '/api/system/status')
        first = self.submit(wid, 'CONTROL:redis-down', key='same-request')
        duplicate = self.submit(wid, 'CONTROL:redis-down', key='same-request', expected=200)
        final = self.terminal(wid, first)
        self.start_redis()
        recovered = self.wait(lambda: (body if (body := self.request('GET', '/api/system/status'))['redis']['available'] else None))
        self.record('real_redis_outage_mysql_dedup_and_recovery', {
            'redis_degraded_observed': degraded['redis']['degraded'] is True,
            'duplicate_reuses_mysql_task': duplicate['id'] == first['id'] and duplicate['reused'] is True,
            'task_completes_without_redis': final['status'] == 'SUCCEEDED',
            'one_worker_request': self.worker.count('CONTROL:redis-down') == 1,
            'redis_recovers_after_restart': recovered['redis']['available'] is True},
            {'degraded_status': degraded, 'first_task': first, 'duplicate_task': duplicate,
             'completed_task': final, 'recovered_status': recovered})

        wid = self.workspace('real-redis-rate-limit')
        first = self.submit(wid, 'CONTROL:rate-first', key='rate-first')
        duplicate = self.submit(wid, 'CONTROL:rate-first', key='rate-first', expected=200)
        second = self.submit(wid, 'CONTROL:rate-second', key='rate-second')
        third = self.http.post(self.base + f'/api/workspaces/{wid}/tasks', headers={'Idempotency-Key': 'rate-third'},
                               json={'question': 'CONTROL:rate-third', 'strategy': 'bm25', 'mode': 'extractive', 'top_k': 1})
        final_first, final_second = self.terminal(wid, first), self.terminal(wid, second)
        self.record('redis_atomic_rate_limit_duplicate_is_free', {
            'duplicate_reuses_task': duplicate['id'] == first['id'] and duplicate['reused'] is True,
            'second_unique_accepted': second['id'] != first['id'],
            'third_unique_rejected_429': third.status_code == 429,
            'rejected_request_never_dispatched': self.worker.count('CONTROL:rate-third') == 0,
            'accepted_tasks_complete': final_first['status'] == final_second['status'] == 'SUCCEEDED'},
            {'first': final_first, 'duplicate': duplicate, 'second': final_second,
             'third_http_status': third.status_code, 'third_response': third.json()})
        self.summary['final_system_status'] = self.request('GET', '/api/system/status')

    def cleanup(self):
        self.stop_jvm()
        self.stop_redis()
        if self.worker:
            self.worker.stop()
        for handle in self.files:
            handle.close()
        self.http.close()
        self.summary['cleanup'] = {'owned_jvm_stopped': self.jvm is None, 'owned_redis_stopped': self.redis is None,
                                   'controlled_worker_stopped': True, 'database_retained': self.database,
                                   'private_logs_directory': str(self.private.relative_to(ROOT))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, default=ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar')
    parser.add_argument('--java', default='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java')
    parser.add_argument('--mysql', default='/opt/homebrew/bin/mysql')
    parser.add_argument('--mysql-socket', default=str(ROOT / '.local/mysql.sock'))
    parser.add_argument('--redis-binary', default='/tmp/lablens-tooling/redis-8.2.2/src/redis-server')
    parser.add_argument('--jvm-port', type=int, default=8082)
    parser.add_argument('--worker-port', type=int, default=8767)
    parser.add_argument('--redis-port', type=int, default=6391)
    parser.add_argument('--output', default=str(ROOT / 'docs/evaluations/v0.12/faults'))
    args = parser.parse_args()
    args.jar = args.jar.resolve()
    experiment = Experiment(args)
    exit_code = 0
    try:
        experiment.prepare()
        experiment.run_cases()
        experiment.summary['status'] = 'passed'
    except Exception as exc:
        exit_code = 1
        experiment.summary['status'] = 'failed'
        experiment.summary['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        print(json.dumps({'status': 'failed', 'type': type(exc).__name__, 'message': str(exc)}), flush=True)
    finally:
        experiment.cleanup()
        experiment.summary['finished_at'] = timestamp()
        experiment.summary['worker_request_counts'] = dict(Counter(event['question'] for event in experiment.worker.events)) if experiment.worker else {}
        experiment.save()
    print(json.dumps({'status': experiment.summary['status'], 'cases': len(experiment.summary['cases']),
                      'report': str(experiment.output / 'summary.json')}), flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
