#!/usr/bin/env python3
"""Actual JVM/MySQL/Redis/Python cancellation with a gated loopback provider.

The provider supplies scripted JSON decisions and synthetic usage. These are
transport and durability fault proofs, not model-quality or production metrics.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import threading
import time

from evaluate_workspace_agent_fault import Experiment, ROOT, now, sha


class GatedProvider:
    def __init__(self, port):
        self.events = []
        self.case = 'running'
        self.release = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                gate = owner.release
                event = dict(case=owner.case, path=self.path, received_at=now(), request=payload)
                owner.events.append(event)
                if not (owner.case == 'nested-embedding' and self.path == '/v1/chat/completions') and not gate.wait(35):
                    event['gate_expired'] = True
                response = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': json.dumps({'decision': {'action': 'search_workspace',
                        'arguments': {'query': 'synthetic accuracy', 'strategy': 'dense' if owner.case == 'nested-embedding' else 'bm25', 'top_k': 3}}})}}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}}
                if self.path == '/v1/embeddings':
                    response = {'data': [{'index': index, 'embedding': [1.0, 0.0]} for index, _ in enumerate(payload['input'])],
                                'usage': {'prompt_tokens': 5, 'total_tokens': 5}}
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
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class CancellationExperiment(Experiment):
    def __init__(self, args):
        super().__init__(args)
        self.database = 'lablens_round2_cancel_' + self.run_id
        self.summary.update(database=self.database,
            scope='Actual local JVM/MySQL/Redis/Python; controlled delayed JSON provider, zero external requests.',
            configuration=dict(agent_total_seconds=75, workers=1, worker_timeout_seconds=90,
                               lease_seconds=120, provider_gate_seconds_maximum=35),
            limitations=['Scripted provider decisions and synthetic token usage are transport controls, not inference.',
                        'Cancellation is cooperative; an already-sent provider request is not forcibly stopped.',
                        'One local Python process only; no distributed worker registry or checkpoint resumption.',
                        'Worker-unreachable cancellation intentionally remains pending and blocks the conversation.'])
        self.inventory = [path for path in self.inventory if path.resolve() != args.jar.resolve()]
        self.inventory += [Path(__file__).resolve(), ROOT / 'lablens/cancellation.py']
        self.summary['source_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in self.inventory}

    def start_python(self):
        self.python = self.start('python', [sys.executable, '-m', 'uvicorn', 'lablens.api:app', '--host', '127.0.0.1', '--port', str(self.args.python_port)])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.python_port}/health').status_code == 200)

    def start_jvm(self):
        self.jvm = self.start('jvm', [self.args.java, '-jar', str(self.frozen_jar)])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.jvm_port}/api/system/status').status_code == 200, seconds=45)

    def prepare(self):
        self.frozen_jar = self.private / 'workspace.jar'
        shutil.copyfile(self.args.jar, self.frozen_jar)
        self.summary['execution_jar'] = dict(source=str(self.args.jar.relative_to(ROOT)),
            immutable_copy=str(self.frozen_jar.relative_to(ROOT)), sha256=sha(self.frozen_jar))
        for port in (self.args.jvm_port, self.args.python_port, self.args.provider_port, self.args.redis_port):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        self.sql(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4; GRANT ALL ON `{self.database}`.* TO 'lablens'@'127.0.0.1';")
        self.env['LABLENS_DB_URL'] = re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+', r'\g<1>' + self.database, self.env['LABLENS_DB_URL'])
        if self.database not in self.env['LABLENS_DB_URL']:
            raise RuntimeError('Could not select isolated database')
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            self.env.pop(key, None)
        self.env.update(NO_PROXY='127.0.0.1,localhost,::1', LABLENS_WORKSPACE_BIND='127.0.0.1',
            LABLENS_WORKSPACE_PORT=str(self.args.jvm_port), LABLENS_REDIS_HOST='127.0.0.1',
            LABLENS_REDIS_PORT=str(self.args.redis_port), LABLENS_REDIS_PASSWORD='',
            LABLENS_WORKER_BASE_URL=f'http://127.0.0.1:{self.args.python_port}', LABLENS_WORKERS='1',
            LABLENS_TASK_LEASE_SECONDS='120', LABLENS_WORKER_TIMEOUT_SECONDS='90', LABLENS_RATE_LIMIT='120',
            LABLENS_CACHE_NAMESPACE='cancellation-fault-' + self.run_id, LABLENS_AGENT_TOTAL_SECONDS='75',
            LABLENS_AGENT_TOOL_BASE_URL=f'http://127.0.0.1:{self.args.python_port}', LABLENS_CONTEXT_WINDOW_TOKENS='65536',
            LLM_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', LLM_MODEL='controlled-json-transport-fixture',
            LLM_API_KEY='local-fixture-not-a-secret', LLM_TIMEOUT_SECONDS='40',
            EMBEDDING_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', EMBEDDING_MODEL='unused-controlled-fixture',
            EMBEDDING_API_KEY='local-fixture-not-a-secret', RAG_TIMEOUT_SECONDS='40', RAG_TOTAL_SECONDS='60')
        self.summary['provider_origins'] = dict(generation=self.env['LLM_BASE_URL'], embedding=self.env['EMBEDDING_BASE_URL'])
        self.provider = GatedProvider(self.args.provider_port)
        self.provider.start()
        self.start('redis', [self.args.redis, '--bind', '127.0.0.1', '--port', str(self.args.redis_port), '--dir', str(self.private), '--save', '', '--appendonly', 'no'])
        self.start_python()
        self.start_jvm()
        self.summary['initial_system_status'] = self.request('GET', '/api/system/status')

    def record(self, name, checks, **evidence):
        case = dict(name=name, passed=all(checks.values()), checks=checks, **evidence)
        self.summary['cases'].append(case)
        self.save()
        print(json.dumps(dict(case=name, passed=case['passed'], checks=len(checks))), flush=True)
        if not case['passed']:
            raise AssertionError('Failed checks: ' + ', '.join(k for k, v in checks.items() if not v))

    def new_turn(self, label, strategy='bm25'):
        w = self.request('POST', '/api/workspaces', expected=201, json={'name': label})['id']
        self.request('PUT', f'/api/workspaces/{w}/documents/control', json={'title': 'Synthetic source',
            'text': 'Synthetic validation accuracy is 86 percent. Transport fixture only.', 'source': 'synthetic:cancellation'})
        c = self.request('POST', f'/api/workspaces/{w}/conversations', expected=201, json={'title': label})['id']
        body = dict(question='Read the synthetic accuracy.', strategy=strategy, mode='agent', top_k=3,
                    approved_revision=1, expected_conversation_revision=0)
        task = self.request('POST', f'/api/workspaces/{w}/conversations/{c}/turns', expected=202,
                            headers={'Idempotency-Key': label}, json=body)
        return w, c, task, body

    def task(self, w, tid):
        return self.request('GET', f'/api/workspaces/{w}/tasks/{tid}')

    def cancelled(self, w, tid):
        task = self.task(w, tid)
        return task if task['status'] == 'CANCELLED' else None

    def run_cases(self):
        w, c, running, body = self.new_turn('gated-running')
        tid = running['id']
        self.wait(lambda: len(self.provider.events) == 1, seconds=10)
        queued_body = dict(question='Queued control', strategy='bm25', mode='agent', top_k=1, approved_revision=1)
        queued = self.request('POST', f'/api/workspaces/{w}/tasks', expected=202,
            headers={'Idempotency-Key': 'queued'}, json=queued_body)
        queued_cancel = self.request('POST', f'/api/workspaces/{w}/tasks/{queued["id"]}/cancel')
        queued_replay = self.request('POST', f'/api/workspaces/{w}/tasks', headers={'Idempotency-Key': 'queued'}, json=queued_body)
        queued_repeat = self.request('POST', f'/api/workspaces/{w}/tasks/{queued["id"]}/cancel')
        self.record('queued_cancellation_zero_dispatch', dict(queued_observed=queued['status'] == 'QUEUED',
            cancelled=queued_cancel['status'] == 'CANCELLED', known_zero=queued_cancel['execution_usage']['model_calls'] == 0
                and queued_cancel['usage_unknown'] is False, no_worker_start=queued_cancel['started_at'] is None,
            repeated_identical=queued_repeat == queued_cancel, replay_same_cancelled=queued_replay['id'] == queued['id']
                and queued_replay['status'] == 'CANCELLED' and queued_replay['reused'] is True,
            provider_count_unchanged=len(self.provider.events) == 1), final=queued_cancel, replay=queued_replay)

        pending = self.request('POST', f'/api/workspaces/{w}/tasks/{tid}/cancel', expected=202)
        repeated = self.request('POST', f'/api/workspaces/{w}/tasks/{tid}/cancel', expected=202)
        blocked = self.request('POST', f'/api/workspaces/{w}/conversations/{c}/turns', expected=409,
            headers={'Idempotency-Key': 'blocked'}, json={**body, 'expected_conversation_revision': 1})
        time.sleep(1.2)
        before_release = self.task(w, tid)
        self.provider.release.set()
        final = self.wait(lambda: self.cancelled(w, tid), seconds=10)
        time.sleep(1.1)
        refreshed = self.task(w, tid)
        history = self.request('GET', f'/api/workspaces/{w}/conversations/{c}')
        trace = (refreshed.get('error_result') or {}).get('agent') or {}
        self.record('running_cooperative_stop_and_late_result_guard', dict(
            pending_active=pending['status'] == repeated['status'] == before_release['status'] == 'CANCEL_REQUESTED',
            pending_usage_unknown=pending['usage_unknown'] is True, next_turn_blocked=blocked.get('error') == 'conversation_busy',
            terminal_cancelled=refreshed['status'] == 'CANCELLED', acknowledged=refreshed['cancel_acknowledged_at'] is not None,
            no_late_report=refreshed['result'] is None, no_followup_provider=len(self.provider.events) == 1,
            known_usage_preserved=refreshed['execution_usage']['model_calls'] == 1 and refreshed['usage_unknown'] is False,
            tokens_preserved=refreshed['execution_usage']['generation_tokens'] == dict(prompt_tokens=8, completion_tokens=4, total_tokens=12),
            trace_preserved=trace.get('state') == 'cancelled' and trace.get('tool_calls') == 0,
            cancelled_history=history['turns'][0]['status'] == 'CANCELLED' and history['turns'][0]['answer'] is None),
            pending=pending, blocked=blocked, final=final, refreshed=refreshed, conversation=history)

        self.provider.case = 'nested-embedding'
        self.provider.release = threading.Event()
        wn, cn, nested, nested_body = self.new_turn('nested-embedding', strategy='dense')
        self.wait(lambda: any(event['case'] == 'nested-embedding' and event['path'] == '/v1/embeddings' for event in self.provider.events), seconds=10)
        nested_pending = self.request('POST', f'/api/workspaces/{wn}/tasks/{nested["id"]}/cancel', expected=202)
        time.sleep(1.2)
        nested_before = self.task(wn, nested['id'])
        self.provider.release.set()
        nested_final = self.wait(lambda: self.cancelled(wn, nested['id']), seconds=10)
        time.sleep(1.1)
        nested_final = self.task(wn, nested['id'])
        nested_events = [event for event in self.provider.events if event['case'] == 'nested-embedding']
        self.record('nested_retrieval_cancellation_stops_second_embedding', dict(
            pending_until_child_exits=nested_pending['status'] == nested_before['status'] == 'CANCEL_REQUESTED',
            cancelled=nested_final['status'] == 'CANCELLED', no_late_report=nested_final['result'] is None,
            exactly_one_decision=sum(e['path'] == '/v1/chat/completions' for e in nested_events) == 1,
            exactly_one_embedding=sum(e['path'] == '/v1/embeddings' for e in nested_events) == 1,
            model_usage_retained=nested_final['execution_usage']['model_calls'] == 1,
            embedding_usage_retained=nested_final['execution_usage']['embedding_requests'] == 1,
            embedding_tokens_retained=nested_final['execution_usage']['embedding_tokens'] == 5,
            known_usage=nested_final['usage_unknown'] is False), pending=nested_pending, final=nested_final, provider_events=nested_events)

        # A second genuine in-flight call is interrupted by owned process failure.
        self.provider.case = 'worker-unreachable'
        self.provider.release = threading.Event()
        w2, c2, running2, body2 = self.new_turn('restart-pending')
        tid2 = running2['id']
        self.wait(lambda: any(event['case'] == 'worker-unreachable' for event in self.provider.events), seconds=10)
        pending2 = self.request('POST', f'/api/workspaces/{w2}/tasks/{tid2}/cancel', expected=202)
        self.jvm.kill(); self.jvm.wait(5)
        self.python.kill(); self.python.wait(5)
        self.provider.release.set()
        self.start_jvm()
        after_jvm_restart = self.task(w2, tid2)
        time.sleep(2.2)
        unreachable = self.task(w2, tid2)
        blocked2 = self.request('POST', f'/api/workspaces/{w2}/conversations/{c2}/turns', expected=409,
            headers={'Idempotency-Key': 'still-blocked'}, json={**body2, 'expected_conversation_revision': 1})
        self.start_python()
        recovered = self.wait(lambda: self.cancelled(w2, tid2), seconds=10)
        history2 = self.request('GET', f'/api/workspaces/{w2}/conversations/{c2}')
        self.record('durable_pending_survives_restart_and_worker_unreachable', dict(
            pending_persisted=pending2['status'] == after_jvm_restart['status'] == unreachable['status'] == 'CANCEL_REQUESTED',
            no_fake_stop_ack=unreachable['cancel_acknowledged_at'] is None,
            conversation_still_busy=blocked2.get('error') == 'conversation_busy',
            restarted_worker_acknowledges=recovered['status'] == 'CANCELLED' and recovered['cancel_acknowledged_at'] is not None,
            lost_process_usage_remains_unknown=recovered['usage_unknown'] is True and recovered['execution_usage']['model_calls'] is None,
            no_provider_retry=sum(event['case'] == 'worker-unreachable' for event in self.provider.events) == 1,
            no_late_report=recovered['result'] is None,
            history_restored=history2['turns'][0]['status'] == 'CANCELLED'), pending=pending2,
            after_jvm_restart=after_jvm_restart, worker_unreachable=unreachable, blocked=blocked2, final=recovered)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/services.env'))
    parser.add_argument('--mysql-socket', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock'))
    parser.add_argument('--mysql', default='/opt/homebrew/opt/mysql/bin/mysql')
    parser.add_argument('--java', default='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java')
    parser.add_argument('--redis', default='/tmp/lablens-tooling/redis-8.2.2/src/redis-server')
    parser.add_argument('--jar', type=Path, default=ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/conversation-round2/cancellation-fault')
    parser.add_argument('--jvm-port', type=int, default=8084)
    parser.add_argument('--python-port', type=int, default=8768)
    parser.add_argument('--provider-port', type=int, default=8770)
    parser.add_argument('--redis-port', type=int, default=6392)
    args = parser.parse_args()
    ports = {args.jvm_port, args.python_port, args.provider_port, args.redis_port}
    if len(ports) != 4 or ports & {8081, 8083, 8765, 8766, 8767, 6380}:
        parser.error('Distinct isolated ports required; main and parent QA ports cannot be reused')
    experiment = CancellationExperiment(args)
    try:
        experiment.prepare()
        experiment.run_cases()
        experiment.summary['status'] = 'passed'
    except Exception as exc:
        experiment.summary['status'] = 'failed'
        experiment.summary['failure'] = dict(type=type(exc).__name__,
            message=str(exc) if isinstance(exc, AssertionError) else 'Inspect private logs for the controlled failure.')
    finally:
        experiment.cleanup()
    print(json.dumps(dict(status=experiment.summary['status'], cases=len(experiment.summary['cases']),
                         report=str(experiment.out.relative_to(ROOT) / 'summary.json'))), flush=True)
    return 0 if experiment.summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
