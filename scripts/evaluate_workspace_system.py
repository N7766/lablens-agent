#!/usr/bin/env python3
"""HTTP-only system controls and closed-loop load against real JVM/MySQL/Redis/RAG.

All tasks use BM25/extractive synthetic controls: zero model/embedding requests.
Unique workspaces and their proof records are retained; no database cleanup,
server restart, fault injection, or direct database access is performed.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'SUCCEEDED', 'FAILED'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def distribution(values):
    values = [value for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    return {'n': len(values), 'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95),
            'min_ms': round(min(values), 3) if values else None,
            'max_ms': round(max(values), 3) if values else None}


def timestamp_delta_ms(earlier, later):
    if earlier is None or later is None:
        return None
    start = datetime.fromisoformat(earlier.replace('Z', '+00:00'))
    end = datetime.fromisoformat(later.replace('Z', '+00:00'))
    value = (end - start).total_seconds() * 1000
    if value < 0:
        raise ValueError('Persisted task timestamps run backwards')
    return round(value, 3)


def task_timing(task):
    return {'queue_ms': timestamp_delta_ms(task.get('created_at'), task.get('started_at')),
            'persisted_end_to_end_ms': timestamp_delta_ms(task.get('created_at'), task.get('finished_at')),
            'persisted_execution_ms': timestamp_delta_ms(task.get('started_at'), task.get('finished_at')),
            'reported_worker_ms': task.get('worker_ms')}


def load_metrics(rows, wall_seconds):
    success = [row for row in rows if row.get('task', {}).get('status') == 'SUCCEEDED']
    return {'submitted_attempts': len(rows), 'succeeded': len(success), 'failed_or_unobserved': len(rows) - len(success),
            'wall_seconds_including_polling': round(wall_seconds, 6),
            'successful_tasks_per_second': round(len(success) / wall_seconds, 6) if wall_seconds > 0 else None,
            'submission_latency': distribution([row.get('submission_ms') for row in rows]),
            'client_end_to_end_latency_including_polling': distribution([row.get('end_to_end_ms') for row in rows]),
            'persisted_queue_latency': distribution([row.get('timing', {}).get('queue_ms') for row in rows]),
            'persisted_end_to_end_latency': distribution([row.get('timing', {}).get('persisted_end_to_end_ms') for row in rows]),
            'worker_execution_latency': distribution([row.get('timing', {}).get('reported_worker_ms') for row in rows]),
            'cache_hits': sum(bool(row.get('task', {}).get('cache_hit')) for row in rows),
            'error_task_ids': [row.get('task', {}).get('id') for row in rows if row.get('task', {}).get('status') != 'SUCCEEDED']}


def source_hashes():
    files = [ROOT / 'scripts/evaluate_workspace_system.py', ROOT / 'lablens/rag.py', ROOT / 'lablens/rag_api.py',
             ROOT / 'services/workspace/pom.xml', ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar']
    files += list((ROOT / 'services/workspace/src/main').rglob('*'))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(files)) if path.is_file()}


class Observer:
    def __init__(self, endpoint, directory, poll_interval, task_timeout):
        self.endpoint = endpoint.rstrip('/')
        self.client = httpx.Client(trust_env=False, timeout=15, follow_redirects=False,
                                  limits=httpx.Limits(max_connections=40, max_keepalive_connections=30))
        self.log = (directory / 'observations.jsonl').open('w', encoding='utf-8')
        self.lock = threading.Lock()
        self.sequence = 0
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout

    def request(self, method, path, body=None, key=None):
        start = time.perf_counter()
        try:
            response = self.client.request(method, self.endpoint + path, json=body,
                                           headers={'Idempotency-Key': key} if key else None)
            try:
                payload = response.json() if response.content else None
            except ValueError:
                payload = {'invalid_json': True}
            result = {'http_status': response.status_code, 'body': payload}
        except httpx.HTTPError as error:
            result = {'http_status': None, 'body': None, 'transport_error_type': type(error).__name__}
        result['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 3)
        with self.lock:
            self.sequence += 1
            event = {'sequence': self.sequence, 'observed_at_utc': now(), 'method': method, 'path': path,
                     'request_body': body, 'idempotency_key': key, **result}
            self.log.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.log.flush()
        return result

    def require(self, method, path, expected, body=None, key=None):
        response = self.request(method, path, body, key)
        if response['http_status'] not in expected:
            raise RuntimeError(f'{method} {path}: expected {sorted(expected)}, observed {response["http_status"]}')
        return response

    def complete(self, workspace, task_id):
        deadline = time.monotonic() + self.task_timeout
        while True:
            response = self.require('GET', f'/api/workspaces/{workspace}/tasks/{task_id}', {200})
            task = response['body']
            if task['status'] in TERMINAL:
                return task
            if time.monotonic() >= deadline:
                return {**task, 'observation_timeout': True}
            time.sleep(self.poll_interval)

    def submit_and_wait(self, workspace, key, body):
        start = time.perf_counter()
        response = self.request('POST', f'/api/workspaces/{workspace}/tasks', body, key)
        result = {'key': key, 'request': body, 'submission_http_status': response['http_status'],
                  'submission_ms': response['elapsed_ms'], 'submission': response['body']}
        if response['http_status'] in {200, 202}:
            result['task'] = self.complete(workspace, response['body']['id'])
            result['timing'] = task_timing(result['task'])
        else:
            result['task'] = {'status': 'SUBMISSION_FAILED'}
        result['end_to_end_ms'] = round((time.perf_counter() - start) * 1000, 3)
        return result

    def close(self):
        self.client.close()
        self.log.close()


def check(summary, name, passed, evidence):
    summary['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})


def body(question):
    return {'question': question, 'strategy': 'bm25', 'mode': 'extractive', 'top_k': 5}


def usage_zero(task):
    usage = (task.get('result') or {}).get('usage') or {}
    return all(usage.get(name) == 0 for name in ('model_calls', 'embedding_requests', 'embedded_inputs'))


def render(summary):
    lines = ['# JVM / MySQL / Redis system evaluation', '',
        'Real HTTP service controls with explicitly synthetic documents and BM25/extractive tasks. No model calls, database mocks, direct database operations, restart or cleanup in this evaluator.', '',
        f"Run: {summary['run_id']}", f"Checks: {sum(item['passed'] for item in summary['checks'])}/{len(summary['checks'])}", '',
        '| Check | Passed |', '| --- | --- |']
    for item in summary['checks']:
        lines.append(f"| {item['name']} | {item['passed']} |")
    if summary.get('load'):
        load = summary['load']['metrics']
        lines += ['', '## Local closed-loop workload', '',
                  f"{load['succeeded']}/{load['submitted_attempts']} succeeded; {load['successful_tasks_per_second']} observed successful tasks/s over {load['wall_seconds_including_polling']} seconds.",
                  f"Client end-to-end p50/p95: {load['client_end_to_end_latency_including_polling']['p50_ms']} / {load['client_end_to_end_latency_including_polling']['p95_ms']} ms.",
                  f"Persisted queue p50/p95: {load['persisted_queue_latency']['p50_ms']} / {load['persisted_queue_latency']['p95_ms']} ms.", '',
                  'These numbers include submission and polling overhead, a tiny synthetic corpus, loopback networking and a single local JVM. They do not measure maximum throughput, a production SLA, LLM serving capacity, or independent-user activity.']
    lines += ['', 'Every observed HTTP response, including polling and metrics, is retained in observations.jsonl. Workspaces and task history are retained for restart checks.']
    return '\n'.join(lines) + '\n'


def self_test():
    assert distribution([1, 2, 3, 4])['p50_ms'] == 2.5
    assert distribution([1, 2, 3, 4])['p95_ms'] == 3.85
    assert distribution([])['p95_ms'] is None
    assert timestamp_delta_ms('2026-01-01T00:00:00.001Z', '2026-01-01T00:00:00.006Z') == 5
    assert timestamp_delta_ms(None, '2026-01-01T00:00:00Z') is None
    try:
        timestamp_delta_ms('2026-01-01T00:00:01Z', '2026-01-01T00:00:00Z')
    except ValueError:
        pass
    else:
        raise AssertionError('Negative persisted durations must not be quietly accepted')
    rows = [{'task': {'status': 'SUCCEEDED', 'cache_hit': True}, 'submission_ms': 3,
             'end_to_end_ms': 10, 'timing': {'queue_ms': 5}},
            {'task': {'status': 'FAILED', 'id': 'failed'}, 'submission_ms': 5, 'end_to_end_ms': 20, 'timing': {}}]
    measured = load_metrics(rows, 2)
    assert measured['successful_tasks_per_second'] == .5
    assert measured['failed_or_unobserved'] == 1
    assert measured['cache_hits'] == 1
    assert measured['client_end_to_end_latency_including_polling']['n'] == 2
    assert measured['persisted_queue_latency']['n'] == 1
    assert usage_zero({'result': {'usage': {'model_calls': 0, 'embedding_requests': 0, 'embedded_inputs': 0}}})
    assert not usage_zero({'result': {'usage': {'model_calls': 0}}})
    print('12 metric and usage assertions passed; no HTTP requests or files written.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8081')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/v0.12/system-local')
    parser.add_argument('--poll-interval', type=float, default=.1)
    parser.add_argument('--task-timeout', type=float, default=60)
    parser.add_argument('--load-tasks', type=int, default=32)
    parser.add_argument('--clients', type=int, default=8)
    parser.add_argument('--smoke-only', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--environment-json', type=Path, help='Optional sanitized deployment version/hardware manifest authored by the operator')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not .05 <= args.poll_interval <= 1 or not 1 <= args.load_tasks <= 80 or not 1 <= args.clients <= 16:
        parser.error('poll-interval .05..1, load-tasks 1..80, clients 1..16')
    args.output.mkdir(parents=True, exist_ok=True)
    if any((args.output / name).exists() for name in ['summary.json', 'observations.jsonl']):
        parser.error('Output already contains results; choose a new directory to preserve evidence')
    run_id = str(uuid4())
    summary = {'schema_version': 1, 'run_id': run_id, 'started_at_utc': now(), 'endpoint': args.endpoint,
               'fixture_scope': 'Synthetic sentinel documents; actual MySQL, Redis, JVM and Python HTTP services; no real experiment outcome claims.',
               'client_environment': {'os': platform.system(), 'os_release': platform.release(), 'architecture': platform.machine(),
                                      'logical_cpus': os.cpu_count(), 'python': platform.python_version()},
               'source_hashes_before': source_hashes(), 'checks': [], 'fixtures': {},
               'configuration': {'duplicate_concurrency': 16, 'load_tasks': 0 if args.smoke_only else args.load_tasks,
                                 'closed_loop_clients': args.clients, 'poll_interval_seconds': args.poll_interval,
                                 'task_timeout_seconds': args.task_timeout, 'strategy': 'bm25', 'mode': 'extractive'},
               'limitations': ['Single local run with loopback networking, no maximum-throughput or production SLA inference.',
                              'Synthetic control documents, no model/embedding calls for this workload.',
                              'Observation polling is included in client end-to-end and throughput windows.',
                              'Workspace scoping does not demonstrate authentication or multi-user authorization.',
                              'Live-process concurrency counters describe this JVM, not a distributed global quota.',
                              'No benchmark seed substitution, automatic retry, hidden warm-up or destructive cleanup.']}
    if args.environment_json:
        summary['operator_environment'] = json.loads(args.environment_json.read_text())
    observer = Observer(args.endpoint, args.output, args.poll_interval, args.task_timeout)
    try:
        initial = observer.require('GET', '/api/system/status', {200})['body']
        summary['system_before'] = initial
        for metric in ['system.cpu.count', 'jvm.memory.max', 'process.uptime']:
            summary.setdefault('actuator_before', {})[metric] = observer.request('GET', '/actuator/metrics/' + metric)
        if initial.get('database') != 'available' or not initial.get('workers', {}).get('enabled'):
            raise RuntimeError('Actual database and enabled dispatcher are required')
        a = observer.require('POST', '/api/workspaces', {201}, {'name': 'Synthetic system controls A ' + run_id})['body']['id']
        b = observer.require('POST', '/api/workspaces', {201}, {'name': 'Synthetic load controls B ' + run_id})['body']['id']
        summary['fixtures'].update(workspace_a=a, workspace_b=b)
        doc_a = {'title': 'Synthetic alpha sentinel', 'text': 'Synthetic control only. alpha_sentinel value ORIGINAL_ONE. This text is not a measured experiment result.', 'source': 'synthetic-control:original'}
        helper = {'title': 'Synthetic additional alpha evidence', 'text': 'Synthetic control only. alpha_sentinel extra evidence ORIGINAL_HELPER.', 'source': 'synthetic-control:helper'}
        doc_b = {'title': 'Synthetic load target', 'text': 'Synthetic control only. load_sentinel immutable value LOAD_TARGET. No production or model measurement is asserted.', 'source': 'synthetic-control:load'}
        observer.require('PUT', f'/api/workspaces/{a}/documents/only-a', {200}, doc_a)
        observer.require('PUT', f'/api/workspaces/{a}/documents/helper-a', {200}, helper)
        observer.require('PUT', f'/api/workspaces/{b}/documents/load-b', {200}, doc_b)
        summary['fixtures']['documents_a_initial'] = observer.require('GET', f'/api/workspaces/{a}/documents', {200})['body']
        summary['fixtures']['documents_b'] = observer.require('GET', f'/api/workspaces/{b}/documents', {200})['body']
        duplicate_key = 'duplicate-' + run_id
        request = body('Find alpha_sentinel evidence')
        barrier = threading.Barrier(16)
        def duplicate(_):
            barrier.wait(timeout=20)
            return observer.request('POST', f'/api/workspaces/{a}/tasks', request, duplicate_key)
        with ThreadPoolExecutor(max_workers=16) as pool:
            duplicates = list(pool.map(duplicate, range(16)))
        summary['duplicate_submissions'] = duplicates
        successful = [r for r in duplicates if r['http_status'] in {200, 202}]
        ids = {r['body']['id'] for r in successful}
        check(summary, '16 concurrent identical keys create exactly one task',
              len(successful) == 16 and len(ids) == 1 and sum(r['http_status'] == 202 for r in duplicates) == 1
              and sum(bool(r['body'].get('reused')) for r in successful) == 15,
              {'response_statuses': [r['http_status'] for r in duplicates], 'unique_task_ids': sorted(ids)})
        if len(ids) != 1:
            raise RuntimeError('Cannot continue snapshot checks without exactly one deduplicated task')
        old_id = next(iter(ids))
        old_task = observer.complete(a, old_id)
        summary['original_task'] = old_task
        original_hash = digest(old_task.get('result'))
        tasks_before = observer.require('GET', f'/api/workspaces/{a}/tasks', {200})['body']['tasks']
        check(summary, 'Deduplicated workspace has exactly one persisted task', len(tasks_before) == 1, {'task_ids': [t['id'] for t in tasks_before]})
        conflict = observer.request('POST', f'/api/workspaces/{a}/tasks', body('Different alpha_sentinel request'), duplicate_key)
        check(summary, 'Reusing key with changed request returns 409', conflict['http_status'] == 409, conflict)
        cross_task = observer.request('GET', f'/api/workspaces/{b}/tasks/{old_id}')
        cross_doc = observer.request('GET', f'/api/workspaces/{b}/documents/only-a')
        check(summary, 'Cross-workspace task and document lookup returns 404', cross_task['http_status'] == cross_doc['http_status'] == 404,
              {'task_http_status': cross_task['http_status'], 'document_http_status': cross_doc['http_status']})
        changed_doc = {**doc_a, 'text': 'Synthetic control only. alpha_sentinel value UPDATED_TWO. No previous marker is part of this version.', 'source': 'synthetic-control:updated'}
        update = observer.require('PUT', f'/api/workspaces/{a}/documents/only-a', {200}, changed_doc)['body']
        observer.require('DELETE', f'/api/workspaces/{a}/documents/helper-a', {204})
        remaining = observer.require('GET', f'/api/workspaces/{a}/documents', {200})['body']
        old_again = observer.require('GET', f'/api/workspaces/{a}/tasks/{old_id}', {200})['body']
        reused_after_update = observer.require('POST', f'/api/workspaces/{a}/tasks', {200}, request, duplicate_key)['body']
        summary['after_update'] = {'updated_document': update, 'documents': remaining, 'old_task': old_again, 'reused_original': reused_after_update}
        check(summary, 'Completed old result remains byte-equivalent after update and deletion',
              old_task['status'] == 'SUCCEEDED' and digest(old_again.get('result')) == original_hash
              and 'ORIGINAL_ONE' in canonical(old_again.get('result')) and 'ORIGINAL_HELPER' in canonical(old_again.get('result')),
              {'before_result_sha256': original_hash, 'after_result_sha256': digest(old_again.get('result')),
               'snapshot_revision': old_again['snapshot_revision'], 'current_revision': remaining['revision']})
        check(summary, 'Original key retains original snapshot after document changes', reused_after_update['id'] == old_id
              and reused_after_update['snapshot_revision'] == old_task['snapshot_revision'],
              {'task_id': reused_after_update['id'], 'snapshot_revision': reused_after_update['snapshot_revision']})
        new = observer.submit_and_wait(a, 'new-revision-' + run_id, request)
        summary['new_revision_task'] = new
        new_task = new['task']
        new_result = canonical(new_task.get('result'))
        check(summary, 'New task uses current version and excludes deleted document', new_task['status'] == 'SUCCEEDED'
              and new_task['snapshot_revision'] == remaining['revision'] and new_task['snapshot_document_count'] == 1
              and 'UPDATED_TWO' in new_result and 'ORIGINAL_ONE' not in new_result and 'ORIGINAL_HELPER' not in new_result
              and not new_task['cache_hit'], {'new_task_id': new_task.get('id'), 'snapshot_revision': new_task.get('snapshot_revision'),
              'document_count': new_task.get('snapshot_document_count'), 'cache_hit': new_task.get('cache_hit')})
        cached = observer.submit_and_wait(a, 'cache-reuse-' + run_id, request)
        summary['cache_reuse_task'] = cached
        cache_task = cached['task']
        check(summary, 'Same snapshot/request with different key reuses Redis result', cache_task['status'] == 'SUCCEEDED'
              and cache_task['id'] != new_task['id'] and cache_task['cache_hit'], {'task_id': cache_task.get('id'), 'cache_hit': cache_task.get('cache_hit')})
        check(summary, 'Redis hit reports zero current model and embedding calls', usage_zero(cache_task),
              {'usage': (cache_task.get('result') or {}).get('usage'), 'usage_unknown': cache_task.get('usage_unknown')})
        summary['restart_fixture'] = {'workspace_a': a, 'workspace_b': b, 'task_ids': [old_id, new_task.get('id'), cache_task.get('id')],
            'tasks': [{'id': t['id'], 'status': t['status'], 'result_sha256': digest(t.get('result')), 'snapshot_revision': t['snapshot_revision']}
                      for t in [old_again, new_task, cache_task] if t.get('id')],
            'documents_a_sha256': digest(remaining), 'documents_a': remaining,
            'scope': 'Completed durable records and version/deletion state; queued/running recovery requires separate restart fixture.'}
        if not args.smoke_only:
            status_samples = []
            stopped = threading.Event()
            def sample_status():
                while not stopped.is_set():
                    status_samples.append(observer.request('GET', '/api/system/status'))
                    stopped.wait(args.poll_interval)
            monitor = threading.Thread(target=sample_status)
            monitor.start()
            load_start = time.perf_counter()
            try:
                def one(index):
                    return observer.submit_and_wait(b, f'load-{run_id}-{index:03d}', body(f'Find load_sentinel evidence for synthetic case {index:03d}'))
                with ThreadPoolExecutor(max_workers=args.clients) as pool:
                    rows = list(pool.map(one, range(args.load_tasks)))
            finally:
                wall = time.perf_counter() - load_start
                stopped.set()
                monitor.join(timeout=20)
            summary['load'] = {'rows': rows, 'metrics': load_metrics(rows, wall), 'system_samples': status_samples,
                               'arrival_pattern': 'Closed loop with at most configured clients in flight; next task starts after prior completion is observed.',
                               'cache_condition': 'All task questions and idempotency keys distinct; expected result cache misses; no provider embeddings.'}
            check(summary, 'All distinct load tasks complete successfully', all(row['task']['status'] == 'SUCCEEDED' for row in rows), summary['load']['metrics'])
            check(summary, 'Distinct load requests bypass result cache', all(not row['task'].get('cache_hit') for row in rows), {'cache_hits': summary['load']['metrics']['cache_hits']})
            check(summary, 'Load makes zero model or embedding calls', all(usage_zero(row['task']) for row in rows),
                  {'task_count': len(rows), 'requests': 'bm25/extractive only'})
        final_status = observer.require('GET', '/api/system/status', {200})['body']
        summary['system_after'] = final_status
        check(summary, 'Configured two-worker bound is respected', final_status.get('workers', {}).get('concurrency') == 2
              and final_status.get('workers', {}).get('max_observed_active', 999) <= 2,
              {'before': initial.get('workers'), 'after': final_status.get('workers'),
               'scope': 'Process lifetime max counter; polling samples alone can miss short-lived peaks.'})
        summary['completed'] = True
    except Exception as error:
        summary['completed'] = False
        summary['fatal_error'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        summary['http_observations'] = observer.sequence
        observer.close()
        summary['completed_at_utc'] = now()
        summary['source_hashes_after'] = source_hashes()
        summary['source_files_unchanged'] = summary['source_hashes_before'] == summary['source_hashes_after']
        summary['passed'] = summary.get('completed', False) and all(item['passed'] for item in summary['checks'])
        (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        (args.output / 'report.md').write_text(render(summary))
        if summary.get('restart_fixture'):
            (args.output / 'restart-fixture.json').write_text(json.dumps(summary['restart_fixture'], ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'completed': summary.get('completed'), 'passed': summary['passed'],
                      'checks_passed': sum(item['passed'] for item in summary['checks']), 'checks_total': len(summary['checks']),
                      'source_files_unchanged': summary['source_files_unchanged'], 'load': (summary.get('load') or {}).get('metrics'),
                      'fatal_error': summary.get('fatal_error')}, ensure_ascii=False, indent=2))
    return 0 if summary['passed'] else 2


if __name__ == '__main__':
    sys.exit(main())
