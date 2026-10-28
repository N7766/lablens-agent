#!/usr/bin/env python3
"""Real JVM -> real Python Agent -> controlled loopback provider fault evidence.

No external model, mocked Python Agent, or fabricated Java worker response is used.
The provider is deliberately scripted; these are failure-handling controls, not
model-quality or embedding-quality measurements. Owned processes are always stopped.
"""
from __future__ import annotations

import argparse
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


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Provider:
    """OpenAI-compatible transport fixture; never performs model inference."""
    def __init__(self, port):
        self.case = 'not-started'
        self.events = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case = owner.case
                event = dict(case=case, path=self.path, received_at=now(), request=payload)
                owner.events.append(event)
                if self.path == '/v1/chat/completions':
                    if case == 'decision_timeout':
                        time.sleep(3)
                    response = {'choices': [{'finish_reason': 'tool_calls', 'message': {
                        'role': 'assistant', 'content': None, 'tool_calls': [{
                            'id': 'controlled-search-1', 'type': 'function', 'function': {
                                'name': 'search_workspace', 'arguments': json.dumps({
                                    'query': 'synthetic accuracy', 'strategy': 'dense', 'top_k': 1})}}]}}],
                        'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}}
                elif self.path == '/v1/embeddings':
                    time.sleep(3)
                    response = {'data': [{'index': i, 'embedding': [1.0, 0.0]} for i, _ in enumerate(payload['input'])],
                                'usage': {'prompt_tokens': 3, 'total_tokens': 3}}
                else:
                    response = {'error': 'Unexpected controlled endpoint'}
                body = json.dumps(response).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    event['response_written'] = True
                except (BrokenPipeError, ConnectionResetError):
                    event['response_written'] = False
                event['finished_at'] = now()

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class Experiment:
    def __init__(self, args):
        self.args = args
        self.run_id = uuid.uuid4().hex[:8]
        self.database = 'lablens_v013_fault_' + self.run_id
        self.out = args.output.resolve()
        if (self.out / 'summary.json').exists():
            raise RuntimeError('Output already contains evidence; select a fresh output directory')
        self.out.mkdir(parents=True, exist_ok=True)
        self.private = ROOT / '.local' / 'agent-fault' / self.run_id
        self.private.mkdir(parents=True)
        self.env = {**os.environ, **{k: v for k, v in dotenv_values(args.credentials).items() if v is not None}}
        self.http = httpx.Client(trust_env=False, timeout=5)
        self.processes = []
        self.logs = []
        self.provider = None
        self.observations = []
        self.summary = dict(run_id=self.run_id, started_at=now(), status='running', database=self.database,
            scope='Actual JVM/MySQL/Redis and Python Agent pipeline; local scripted provider delays, no external model inference.',
            external_model_requests=0, external_embedding_requests=0,
            ports=dict(jvm=args.jvm_port, python=args.python_port, provider=args.provider_port, redis=args.redis_port),
            configuration=dict(agent_total_seconds=1, provider_delay_seconds=3, workers=1, worker_timeout_seconds=10, lease_seconds=30),
            cases=[], process_events=[], limitations=[
                'The controlled provider returns a scripted tool choice and synthetic usage for transport testing, not actual inference.',
                'Timeout cannot cancel an already received provider request; no automatic retry is sent.',
                'These two local failures do not establish semantic accuracy, production availability, or distributed recovery.'])
        inventory = [Path(__file__).resolve(), args.jar, ROOT / 'services/workspace/src/main/resources/application.properties']
        inventory += [ROOT / 'lablens' / name for name in ['agent.py', 'workspace_agent.py', 'model.py', 'api.py', 'rag_api.py', 'rag.py', 'assessment.py']]
        inventory += sorted((ROOT / 'services/workspace/src/main/java').rglob('*.java'))
        inventory += sorted((ROOT / 'services/workspace/src/main/resources/db/migration').glob('*.sql'))
        self.inventory = inventory
        self.summary['source_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in inventory}

    def save(self):
        for name, value in [('summary.json', self.summary), ('http-observations.json', self.observations),
                            ('provider-events.json', self.provider.events if self.provider else [])]:
            (self.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def sql(self, statement, database=None):
        args = [self.args.mysql, '--no-defaults', '--socket=' + str(self.args.mysql_socket), '-uroot', '--batch', '--raw', '--skip-column-names']
        if database:
            args.append(database)
        return subprocess.run(args, input=statement, text=True, capture_output=True, check=True).stdout

    def start(self, name, argv, env=None):
        log = (self.private / (name + '.log')).open('w')
        self.logs.append(log)
        proc = subprocess.Popen(argv, cwd=ROOT, env=env or self.env, stdout=log, stderr=subprocess.STDOUT)
        self.processes.append((name, proc))
        self.summary['process_events'].append(dict(event=name + '_start', pid=proc.pid, at=now()))
        return proc

    def wait(self, predicate, seconds=30):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                value = predicate()
                if value:
                    return value
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(.1)
        raise AssertionError('Readiness or terminal state was not reached within the bounded wait')

    def request(self, method, path, expected=200, **kwargs):
        response = self.http.request(method, f'http://127.0.0.1:{self.args.jvm_port}' + path, **kwargs)
        body = response.json()
        self.observations.append(dict(at=now(), method=method, path=path, request=kwargs.get('json'),
                                      http_status=response.status_code, body=body))
        if response.status_code != expected:
            raise AssertionError(f'{method} {path}: expected {expected}, received {response.status_code}')
        return body

    def prepare(self):
        for port in (self.args.jvm_port, self.args.python_port, self.args.provider_port, self.args.redis_port):
            if port in {8081, 8083, 8765, 8766, 6380}:
                raise RuntimeError('Cannot reuse a main or parent QA port')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        self.sql(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4; GRANT ALL ON `{self.database}`.* TO 'lablens'@'127.0.0.1';")
        self.env['LABLENS_DB_URL'] = re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+', r'\g<1>' + self.database, self.env['LABLENS_DB_URL'])
        if self.database not in self.env['LABLENS_DB_URL']:
            raise RuntimeError('Could not select the isolated database')
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            self.env.pop(key, None)
        self.env.update(NO_PROXY='127.0.0.1,localhost,::1', LABLENS_WORKSPACE_BIND='127.0.0.1',
            LABLENS_WORKSPACE_PORT=str(self.args.jvm_port), LABLENS_REDIS_HOST='127.0.0.1', LABLENS_REDIS_PORT=str(self.args.redis_port),
            LABLENS_REDIS_PASSWORD='', LABLENS_WORKER_BASE_URL=f'http://127.0.0.1:{self.args.python_port}', LABLENS_WORKERS='1',
            LABLENS_TASK_LEASE_SECONDS='30', LABLENS_WORKER_TIMEOUT_SECONDS='10', LABLENS_RATE_LIMIT='20',
            LABLENS_CACHE_NAMESPACE='agent-fault-' + self.run_id, LABLENS_AGENT_TOTAL_SECONDS='1',
            LABLENS_AGENT_TOOL_BASE_URL=f'http://127.0.0.1:{self.args.python_port}',
            LLM_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', LLM_MODEL='controlled-transport-fixture',
            LLM_API_KEY='local-fixture-not-a-secret', LLM_TIMEOUT_SECONDS='5',
            EMBEDDING_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', EMBEDDING_MODEL='controlled-vector-fixture',
            EMBEDDING_API_KEY='local-fixture-not-a-secret', EMBEDDING_MODEL_REVISION=self.run_id,
            RAG_TIMEOUT_SECONDS='5', RAG_TOTAL_SECONDS='5')
        self.summary['provider_origins'] = dict(generation=self.env['LLM_BASE_URL'], embedding=self.env['EMBEDDING_BASE_URL'])
        self.provider = Provider(self.args.provider_port)
        self.provider.start()
        self.start('redis', [self.args.redis, '--bind', '127.0.0.1', '--port', str(self.args.redis_port), '--dir', str(self.private), '--save', '', '--appendonly', 'no'])
        self.start('python', [sys.executable, '-m', 'uvicorn', 'lablens.api:app', '--host', '127.0.0.1', '--port', str(self.args.python_port)])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.python_port}/health').status_code == 200)
        self.start('jvm', [self.args.java, '-jar', str(self.args.jar)])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.jvm_port}/api/system/status').status_code == 200, seconds=45)
        self.summary['initial_system_status'] = self.request('GET', '/api/system/status')
        self.summary['versions'] = dict(python=sys.version.split()[0], mysql=self.sql('SELECT VERSION();').strip(),
            redis=subprocess.check_output([self.args.redis, '--version'], text=True).strip(),
            java=subprocess.run([self.args.java, '-version'], capture_output=True, text=True, check=True).stderr.splitlines()[0])

    def run_case(self, name):
        self.provider.case = name
        workspace = self.request('POST', '/api/workspaces', expected=201, json={'name': 'Controlled Agent fault: ' + name})['id']
        self.request('PUT', f'/api/workspaces/{workspace}/documents/control', json={'title': 'Synthetic observation',
            'text': 'Synthetic accuracy was 86 percent. This document is only a transport-failure fixture.', 'source': 'synthetic:agent-fault'})
        revision = self.request('GET', f'/api/workspaces/{workspace}/documents')['revision']
        request = dict(question='Read the synthetic accuracy observation.', strategy='dense' if name == 'embedding_timeout' else 'bm25',
                       mode='agent', top_k=1, approved_revision=revision)
        created = self.request('POST', f'/api/workspaces/{workspace}/tasks', expected=202,
                               headers={'Idempotency-Key': name}, json=request)
        tid = created['id']
        def terminal():
            value = self.request('GET', f'/api/workspaces/{workspace}/tasks/{tid}')
            return value if value['status'] in {'FAILED', 'SUCCEEDED'} else None
        final = self.wait(terminal, seconds=20)
        time.sleep(3.3)  # Allow the deliberately late provider response to finish and detect unexpected retries.
        refreshed = self.request('GET', f'/api/workspaces/{workspace}/tasks/{tid}')
        replay = self.request('POST', f'/api/workspaces/{workspace}/tasks', headers={'Idempotency-Key': name}, json=request)
        db_row = json.loads(self.sql("SELECT JSON_OBJECT('id',id,'status',status,'error_code',error_code,'snapshot_revision',snapshot_revision,"
            "'request',request_json,'snapshot',snapshot_json,'execution_usage',execution_usage_json,'error_result',error_result_json) "
            f"FROM tasks WHERE id='{tid}';", self.database))
        events = [e for e in self.provider.events if e['case'] == name]
        usage = final['execution_usage']
        agent = (final.get('error_result') or {}).get('agent') or {}
        checks = dict(task_failed=final['status'] == 'FAILED', explicit_timeout=final['error_code'] == 'agent_timeout',
            error_result_persisted=bool(final.get('error_result')), trace_persisted=bool(agent.get('trace')),
            snapshot_bound=agent.get('snapshot_id') == workspace + ':' + str(revision),
            document_hash_saved=len(agent.get('documents_sha256', '')) == 64,
            state_timeout=agent.get('state') == 'timeout', known_model_count_preserved=usage.get('model_calls') == 1,
            unknown_usage_marked=usage.get('provider_usage_unknown') is True,
            refreshed_identical=refreshed == final, db_usage_matches_api=db_row['execution_usage'] == usage,
            db_trace_matches_api=db_row['error_result'] == final['error_result'],
            idempotency_replay_same_failed_task=replay['reused'] is True and replay['id'] == tid and replay['status'] == 'FAILED',
            one_model_request=sum(e['path'] == '/v1/chat/completions' for e in events) == 1)
        if name == 'decision_timeout':
            checks.update(no_tools_started=agent.get('tool_calls') == 0,
                no_embedding_request=all(e['path'] != '/v1/embeddings' for e in events),
                generation_usage_unknown=all(v is None for v in usage['generation_tokens'].values()))
        else:
            checks.update(real_search_tool_started=agent.get('tool_calls') == 1 and any(e.get('event') == 'tool_started' and e.get('name') == 'search_workspace' for e in agent['trace']),
                one_embedding_request=sum(e['path'] == '/v1/embeddings' for e in events) == 1,
                decision_usage_preserved=usage['generation_tokens'] == {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12},
                embedding_usage_unknown=usage.get('embedding_tokens') is None)
        case = dict(name=name, passed=all(checks.values()), checks=checks, workspace_id=workspace, task_id=tid,
                    final=final, refreshed=refreshed, replay=replay, database_row=db_row, provider_events=events)
        self.summary['cases'].append(case)
        self.save()
        print(json.dumps(dict(case=name, passed=case['passed'], checks=len(checks), error_code=final.get('error_code'))), flush=True)
        if not case['passed']:
            raise AssertionError('Failed checks: ' + ', '.join(k for k, v in checks.items() if not v))

    def cleanup(self):
        for name, proc in reversed(self.processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=3)
            self.summary['process_events'].append(dict(event=name + '_stop', pid=proc.pid, exit_code=proc.returncode, at=now()))
        if self.provider:
            self.provider.stop()
        for log in self.logs:
            log.close()
        self.http.close()
        self.summary['cleanup'] = dict(all_owned_processes_stopped=all(p.poll() is not None for _, p in self.processes),
            controlled_provider_stopped=not self.provider.thread.is_alive() if self.provider else True,
            database_retained=self.database, private_logs_directory=str(self.private.relative_to(ROOT)))
        self.summary['source_unchanged'] = all(sha(p) == self.summary['source_sha256'][str(p.relative_to(ROOT))] for p in self.inventory)
        self.summary['finished_at'] = now()
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/services.env'))
    parser.add_argument('--mysql-socket', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock'))
    parser.add_argument('--mysql', default='/opt/homebrew/opt/mysql/bin/mysql')
    parser.add_argument('--java', default='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java')
    parser.add_argument('--redis', default='/tmp/lablens-tooling/redis-8.2.2/src/redis-server')
    parser.add_argument('--jar', type=Path, default=ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/v0.13/agent-fault')
    parser.add_argument('--jvm-port', type=int, default=8084)
    parser.add_argument('--python-port', type=int, default=8768)
    parser.add_argument('--provider-port', type=int, default=8770)
    parser.add_argument('--redis-port', type=int, default=6392)
    args = parser.parse_args()
    experiment = Experiment(args)
    try:
        experiment.prepare()
        experiment.run_case('decision_timeout')
        experiment.run_case('embedding_timeout')
        experiment.summary['status'] = 'passed'
    except Exception as exc:
        experiment.summary['status'] = 'failed'
        # Exception messages contain only local validation descriptions; never echo subprocess stderr or environment.
        experiment.summary['failure'] = dict(type=type(exc).__name__, message=str(exc) if isinstance(exc, AssertionError) else 'Inspect private process logs for the controlled failure.')
    finally:
        experiment.cleanup()
    print(json.dumps(dict(status=experiment.summary['status'], cases=len(experiment.summary['cases']),
                         report=str(experiment.out.relative_to(ROOT) / 'summary.json'))), flush=True)
    return 0 if experiment.summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
