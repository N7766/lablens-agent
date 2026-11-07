#!/usr/bin/env python3
"""Bounded open-arrival HTTP admission experiment; retains every scheduled slot.

Requires a dedicated JVM/DB/Redis and controlled_admission_worker.py. Does not
start services, touch databases directly, use providers, retry or clean history.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED')
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED'}


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    def p(fraction):
        if not values:
            return None
        index = (len(values) - 1) * fraction
        lo, hi = math.floor(index), math.ceil(index)
        return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 3)
    return dict(n=len(values), p50_ms=p(.5), p95_ms=p(.95), p99_ms=p(.99),
                max_ms=max(values) if values else None)


def timestamp_delta_ms(earlier, later):
    if not earlier or not later:
        return None
    seconds = (datetime.fromisoformat(later.replace('Z', '+00:00')) -
               datetime.fromisoformat(earlier.replace('Z', '+00:00'))).total_seconds()
    if seconds < 0:
        raise ValueError('Persisted task timestamp goes backwards')
    return round(seconds * 1000, 3)


def planned_slots(duration, rates):
    rows = []
    for stage_index, (stage, rate) in enumerate(zip(('under', 'over', 'recovery'), rates)):
        for number in range(math.ceil(duration * rate - 1e-9)):
            rows.append(dict(sequence=len(rows) + 1, stage=stage,
                             scheduled_offset_s=round(stage_index * duration + number / rate, 9)))
    return rows


def active_count(status):
    counts = status.get('task_counts', {})
    return sum(counts.get(key, 0) for key in ACTIVE)


def stage_metrics(rows):
    counts = Counter(row['outcome'] for row in rows)
    sent = [row for row in rows if row.get('send_offset_s') is not None]
    responded = [row for row in sent if row.get('http_status') is not None]
    return dict(scheduled=len(rows), sent=len(sent), responses=len(responded),
                accepted=counts['accepted'], rejected=counts['rejected'],
                transport_errors=counts['transport_error'], unexpected_http=counts['unexpected_http'],
                unsent=counts['unsent'], unsent_reasons=dict(Counter(row.get('unsent_reason') for row in rows if row['outcome'] == 'unsent')),
                rejection_codes=dict(Counter(row.get('rejection_code') for row in rows if row['outcome'] == 'rejected')),
                submission_latency=distribution([row.get('response_ms') for row in sent]),
                sender_lateness=distribution([row.get('lateness_ms') for row in rows]))


def source_hashes(jar):
    files = [ROOT / 'scripts/evaluate_admission_load.py', ROOT / 'scripts/controlled_admission_worker.py',
             ROOT / 'lablens/rag.py', ROOT / 'docs/evaluations/conversation-round12/protocol.md']
    return {str(path.relative_to(ROOT)): sha(path) for path in files} | {'tested_jar': sha(jar)}


def loopback_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise ValueError('Only an HTTP loopback origin is allowed')
    return value.rstrip('/')


class Evidence:
    def __init__(self, directory):
        self.files = {name: (directory / f'{name}.jsonl').open('x', encoding='utf-8', buffering=1)
                      for name in ('observations', 'requests', 'samples', 'tasks')}
        self.sequence = 0

    def emit(self, name, data):
        self.files[name].write(json.dumps(data, ensure_ascii=False) + '\n')

    async def request(self, client, method, url, body=None, key=None):
        start = time.monotonic()
        try:
            response = await client.request(method, url, json=body,
                                            headers={'Idempotency-Key': key} if key else None)
            try:
                payload = response.json() if response.content else None
            except ValueError:
                payload = {'invalid_json': True}
            result = dict(http_status=response.status_code, body=payload,
                          retry_after=response.headers.get('Retry-After'))
        except httpx.HTTPError as exc:
            result = dict(http_status=None, body=None, transport_error_type=type(exc).__name__)
        result['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
        self.sequence += 1
        self.emit('observations', dict(sequence=self.sequence, observed_at_utc=now(), method=method,
                                      url=url, request_body=body, idempotency_key=key, **result))
        return result

    async def require(self, client, method, url, expected, body=None):
        result = await self.request(client, method, url, body)
        if result['http_status'] not in expected:
            raise RuntimeError(f'{method} {urlsplit(url).path}: unexpected HTTP {result["http_status"]}')
        return result['body']

    def close(self):
        for file in self.files.values():
            file.close()


async def run(args, *, metrics=None, extra_sources=()):
    metric_paths = metrics or {name: name for name in (
        "process.cpu.usage", "system.cpu.usage", "jvm.memory.used", "jvm.memory.max")}
    def current_hashes():
        return source_hashes(args.jar) | {str(path.relative_to(ROOT)): sha(path) for path in extra_sources}
    args.endpoint = loopback_url(args.endpoint)
    args.worker_endpoint = loopback_url(args.worker_endpoint)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError('Evidence directory must be empty; previous runs cannot be overwritten')
    run_id = str(uuid4())
    schedule = planned_slots(args.phase_seconds, (args.under_rate, args.over_rate, args.recovery_rate))
    if len(schedule) > args.max_requests:
        raise ValueError('Scheduled requests exceed max_requests protection')
    hashes = current_hashes()
    manifest = dict(schema_version=1, run_id=run_id, variant=args.variant, created_at_utc=now(),
                    source_commit=args.source_commit, source_hashes=hashes,
                    operator_environment=json.loads(args.environment_json.read_text()) if args.environment_json else None,
                    client_environment=dict(os=platform.system(), architecture=platform.machine(),
                                            logical_cpus=os.cpu_count(), python=platform.python_version()),
                    metric_paths=metric_paths,
                    configuration={key: value for key, value in vars(args).items() if key not in {'output', 'jar', 'environment_json'}},
                    schedule=schedule, limitations=['Synthetic fixed-delay BM25 work, no model capacity inference',
                      'Sampled maxima are not continuous peaks; transaction concurrency tests separately establish the admission invariant',
                      'Sequential local baseline/candidate runs do not control background system load',
                      'Transport errors can leave server acceptance unknown; no automatic retry'])
    dump(args.output / 'manifest.json', manifest)
    evidence = Evidence(args.output)
    rows, samples, tasks, checks = [], [], [], []
    summary = dict(run_id=run_id, variant=args.variant, started_at_utc=now(), completed=False, checks=checks)
    def check(name, passed, detail=None):
        checks.append(dict(name=name, passed=bool(passed), evidence=detail))
    outstanding = set()
    stop = asyncio.Event()
    monitor = None
    peak_in_flight = 0
    started = None
    limits = httpx.Limits(max_connections=args.max_in_flight + 16, max_keepalive_connections=args.max_in_flight + 16)
    async with httpx.AsyncClient(timeout=args.http_timeout, trust_env=False, limits=limits) as client:
        async def request(method, path, body=None, key=None):
            return await evidence.request(client, method, args.endpoint + path, body, key)
        async def require(method, path, expected, body=None):
            return await evidence.require(client, method, args.endpoint + path, expected, body)
        try:
            before = await require('GET', '/api/system/status', {200})
            worker_before = await evidence.require(client, 'GET', args.worker_endpoint + '/control/status', {200})
            summary.update(system_before=before, worker_before=worker_before)
            if before.get('database') != 'available' or before.get('workers', {}).get('concurrency') != 2:
                raise RuntimeError('A live database and exactly two dispatcher workers are required')
            if active_count(before) != 0 or sum(before.get('task_counts', {}).values()) != 0:
                raise RuntimeError('Dedicated empty task database is required')
            if worker_before.get('fixed_delay_ms') != args.worker_delay_ms or worker_before.get('started') != 0:
                raise RuntimeError('Fresh controlled worker with declared fixed delay is required')
            workspaces = []
            for number in range(args.workspaces):
                workspace = await require('POST', '/api/workspaces', {201}, {'name': f'Synthetic admission {run_id} {number}'})
                workspaces.append(workspace['id'])
                await require('PUT', f'/api/workspaces/{workspace["id"]}/documents/synthetic-load', {200},
                              {'title': 'Synthetic admission sentinel', 'source': f'synthetic-admission:{run_id}:{number}',
                               'text': f'Synthetic fixture only. admission_sentinel value ACCEPTANCE_{number}. No user data or model result.'})
            summary['workspaces'] = workspaces
            started = time.monotonic()
            summary['load_started_at_utc'] = now()
            async def sample():
                next_metrics = 0
                while not stop.is_set():
                    offset = time.monotonic() - started
                    response = await request('GET', '/api/system/status')
                    item = dict(offset_s=round(offset, 6), observed_at_utc=now(), system=response)
                    if offset >= next_metrics:
                        item['actuator'] = {}
                        for metric, path in metric_paths.items():
                            if stop.is_set():
                                break
                            item['actuator'][metric] = await request('GET', '/actuator/metrics/' + path)
                        next_metrics = offset + args.metrics_interval
                    samples.append(item)
                    evidence.emit('samples', item)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=args.sample_interval)
                    except asyncio.TimeoutError:
                        pass
            monitor = asyncio.create_task(sample())
            async def submit(slot):
                row = dict(slot)
                row['workspace_id'] = workspaces[(row['sequence'] - 1) % len(workspaces)]
                row['send_offset_s'] = round(time.monotonic() - started, 6)
                row['lateness_ms'] = round((row['send_offset_s'] - row['scheduled_offset_s']) * 1000, 3)
                body = dict(question=f'Find admission_sentinel evidence for synthetic request {run_id} {row["sequence"]}',
                            mode='extractive', strategy='bm25', top_k=5)
                response = await request('POST', f'/api/workspaces/{row["workspace_id"]}/tasks', body,
                                         key=f'admission-{run_id}-{row["sequence"]}')
                row.update(response_offset_s=round(time.monotonic() - started, 6), response_ms=response['elapsed_ms'],
                           http_status=response['http_status'], retry_after=response.get('retry_after'))
                payload = response.get('body') or {}
                if not isinstance(payload, dict):
                    payload = {'invalid_response_type': type(payload).__name__}
                row['response_body'] = payload
                if response['http_status'] in {200, 202} and isinstance(payload.get('id'), str):
                    row.update(outcome='accepted', task_id=payload['id'])
                elif response['http_status'] in {429, 503}:
                    row.update(outcome='rejected', rejection_code=payload.get('error', payload.get('code')))
                elif response['http_status'] is None:
                    row.update(outcome='transport_error', transport_error_type=response['transport_error_type'])
                else:
                    row['outcome'] = 'unexpected_http'
                rows.append(row)
                evidence.emit('requests', row)
            for slot in schedule:
                delay = started + slot['scheduled_offset_s'] - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                lateness = (time.monotonic() - started - slot['scheduled_offset_s']) * 1000
                if lateness > args.max_lateness_ms or len(outstanding) >= args.max_in_flight:
                    row = dict(slot, outcome='unsent', send_offset_s=None, lateness_ms=round(lateness, 3),
                               unsent_reason='scheduler_late' if lateness > args.max_lateness_ms else 'client_in_flight_limit')
                    rows.append(row)
                    evidence.emit('requests', row)
                    continue
                task = asyncio.create_task(submit(slot))
                outstanding.add(task)
                task.add_done_callback(outstanding.discard)
                peak_in_flight = max(peak_in_flight, len(outstanding))
            # Preserve the entire declared final stage even after its last arrival.
            await asyncio.sleep(max(0, started + 3 * args.phase_seconds - time.monotonic()))
            if outstanding:
                await asyncio.gather(*outstanding)
            drain_started = time.monotonic()
            while True:
                after = await require('GET', '/api/system/status', {200})
                if active_count(after) == 0 or time.monotonic() - drain_started >= args.drain_timeout:
                    break
                await asyncio.sleep(.25)
            summary['drain_seconds'] = round(time.monotonic() - drain_started, 3)
            summary['system_after'] = after
            gate = asyncio.Semaphore(8)
            async def observe_terminal(row):
                async with gate:
                    response = await request('GET', f'/api/workspaces/{row["workspace_id"]}/tasks/{row["task_id"]}')
                    task = response.get('body') if response['http_status'] == 200 else None
                    record = dict(sequence=row['sequence'], stage=row['stage'], task_id=row['task_id'],
                                  workspace_id=row['workspace_id'], http_status=response['http_status'], task=task)
                    if isinstance(task, dict):
                        record['timing'] = dict(queue_ms=timestamp_delta_ms(task.get('created_at'), task.get('started_at')),
                            end_to_end_ms=timestamp_delta_ms(task.get('created_at'), task.get('finished_at')),
                            worker_ms=task.get('worker_ms'))
                    tasks.append(record)
                    evidence.emit('tasks', record)
            await asyncio.wait_for(
                asyncio.gather(*(observe_terminal(row) for row in rows if row['outcome'] == 'accepted')),
                timeout=max(.01, args.drain_timeout - (time.monotonic() - drain_started)))
            summary['worker_after'] = await evidence.require(client, 'GET', args.worker_endpoint + '/control/status', {200})
            summary['completed'] = True
        except Exception as exc:
            summary['fatal_error'] = dict(type=type(exc).__name__, message=str(exc))
        finally:
            # HTTP submits are bounded by http_timeout; do not silently abandon accepted records.
            if outstanding:
                await asyncio.gather(*outstanding, return_exceptions=True)
            stop.set()
            if monitor:
                await monitor
            recorded = {row['sequence'] for row in rows}
            for slot in schedule:
                if slot['sequence'] not in recorded:
                    row = dict(slot, outcome='unsent', send_offset_s=None, unsent_reason='run_aborted')
                    rows.append(row)
                    evidence.emit('requests', row)
            evidence.close()
    rows.sort(key=lambda row: row['sequence'])
    counts = stage_metrics(rows)
    summary.update(counts=counts, stages={stage: stage_metrics([row for row in rows if row['stage'] == stage])
                                         for stage in ('under', 'over', 'recovery')},
                   max_client_in_flight=peak_in_flight,
                   actual_load_and_observation_seconds=round(time.monotonic() - started, 3) if started else None,
                   http_observations=evidence.sequence)
    accepted = [row for row in rows if row['outcome'] == 'accepted']
    rejected = [row for row in rows if row['outcome'] == 'rejected']
    live_samples = [sample['system']['body'] for sample in samples if sample['system']['http_status'] == 200]
    active = [active_count(sample) for sample in live_samples]
    ages = [sample.get('admission', {}).get('oldest_queued_age_ms') for sample in live_samples]
    actuator = {}
    for metric in ('process.cpu.usage', 'system.cpu.usage', 'jvm.memory.used', 'jvm.memory.max'):
        values = [measurement['value'] for sample in samples
                  for measurement in ((sample.get('actuator', {}).get(metric, {}).get('body') or {}).get('measurements', []))
                  if measurement.get('statistic') == 'VALUE' and isinstance(measurement.get('value'), (int, float))]
        actuator[metric] = dict(samples=len(values), max=max(values) if values else None,
                               mean=round(sum(values) / len(values), 6) if values else None,
                               availability='observed' if values else 'unavailable')
    summary['actuator_observed'] = actuator
    summary['observed'] = dict(max_sampled_global_active=max(active) if active else None,
                               oldest_queued_age=distribution(ages),
                               accepted_queue_latency=distribution([task.get('timing', {}).get('queue_ms') for task in tasks]),
                               accepted_end_to_end_latency=distribution([task.get('timing', {}).get('end_to_end_ms') for task in tasks]),
                               accepted_worker_latency=distribution([task.get('timing', {}).get('worker_ms') for task in tasks]),
                               terminal_status_counts=dict(Counter((task.get('task') or {}).get('status', 'UNOBSERVED') for task in tasks)))
    check('Every scheduled slot is retained exactly once', len(rows) == len(schedule) == len({row['sequence'] for row in rows}))
    check('Submission accounting balances', counts['scheduled'] == counts['sent'] + counts['unsent'] and
          counts['sent'] == counts['accepted'] + counts['rejected'] + counts['transport_errors'] + counts['unexpected_http'])
    check('No unsent, transport errors or unexpected HTTP', counts['unsent'] == counts['transport_errors'] == counts['unexpected_http'] == 0, counts)
    check('Client concurrency is bounded', peak_in_flight <= args.max_in_flight, peak_in_flight)
    check('Accepted tasks have unique IDs', len(accepted) > 0 and len({row['task_id'] for row in accepted}) == len(accepted))
    check('All accepted tasks observed SUCCEEDED', len(tasks) == len(accepted) > 0 and all((row.get('task') or {}).get('status') == 'SUCCEEDED' for row in tasks))
    check('Accepted tasks have known zero provider use', bool(tasks) and all(
        all(((row.get('task') or {}).get('result') or {}).get('usage', {}).get(key) == 0
            for key in ('model_calls', 'embedding_requests', 'embedded_inputs'))
        and ((row.get('task') or {}).get('result') or {}).get('usage', {}).get('provider_usage_unknown') is False for row in tasks))
    check('Distinct requests bypass result cache', bool(tasks) and all((row.get('task') or {}).get('cache_hit') is False for row in tasks))
    check('No accepted ID in rejected responses', all('id' not in row.get('response_body', {}) for row in rejected))
    workers = summary.get('system_after', {}).get('workers', {})
    check('Dispatcher max concurrency does not exceed two', workers.get('max_observed_active', 999) <= 2)
    check('Controlled worker processed every accepted task once', summary.get('worker_after', {}).get('started') == len(accepted)
          and summary.get('worker_after', {}).get('completed') == len(accepted) and summary.get('worker_after', {}).get('failed') == 0
          and summary.get('worker_after', {}).get('max_active', 999) <= 2)
    check('Activities drain to zero', bool(summary.get('system_after')) and active_count(summary['system_after']) == 0)
    recovery_tail = [row for row in rows if row['stage'] == 'recovery' and row['scheduled_offset_s'] >= args.phase_seconds * 2.5]
    check('Late recovery accepts without capacity rejection', any(row['outcome'] == 'accepted' for row in recovery_tail)
          and not any(row['outcome'] == 'rejected' for row in recovery_tail))
    summary['baseline_global_limit_gap_observed'] = args.variant == 'baseline' and bool(active) and max(active) > args.global_limit
    if args.variant == 'candidate':
        admission = summary.get('system_before', {}).get('admission', {})
        check('Candidate declares configured admission limits', admission.get('global_pending_limit') == args.global_limit
              and admission.get('workspace_pending_limit') == args.workspace_limit, admission)
        check('Sampled global active count within limit', bool(active) and max(active) <= args.global_limit, summary['observed']['max_sampled_global_active'])
        over_rejections = [row for row in rejected if row['stage'] == 'over']
        check('Overload yields queue admission refusals', bool(over_rejections) and all(row.get('rejection_code') in {'global_queue_full', 'queue_full'} for row in over_rejections),
              Counter(row.get('rejection_code') for row in over_rejections))
        check('Capacity refusals contain Retry-After', bool(rejected) and all(str(row.get('retry_after', '')).isdigit() and int(row['retry_after']) > 0 for row in rejected))
    after_hashes = current_hashes()
    summary['source_hashes_after'] = after_hashes
    check('Frozen scripts, protocol, RAG source and tested jar unchanged', hashes == after_hashes)
    summary.update(completed_at_utc=now(), passed=summary['completed'] and all(check['passed'] for check in checks))
    dump(args.output / 'summary.json', summary)
    report = ['# Admission load evidence', '', f"Variant: {args.variant}; passed: {summary['passed']}", '',
              'Synthetic fixed-delay BM25 work; no model serving capacity or production SLA claim.', '',
              '| Stage | Scheduled | Sent | Accepted | Rejected | Unsent | Transport |', '|---|---:|---:|---:|---:|---:|---:|']
    for stage, values in summary['stages'].items():
        report.append('| ' + ' | '.join(str(value) for value in [stage, *[values[key] for key in ('scheduled', 'sent', 'accepted', 'rejected', 'unsent', 'transport_errors')]]) + ' |')
    report += ['', '| Check | Passed |', '|---|---|'] + [f"| {check['name']} | {check['passed']} |" for check in checks]
    report += ['', 'Sampled global active maximum: ' + str(summary['observed']['max_sampled_global_active']),
               'Raw per-request records, all accepted task records and status/actuator samples are retained alongside summary.json.']
    (args.output / 'report.md').write_text('\n'.join(report) + '\n')
    print(json.dumps(dict(passed=summary['passed'], completed=summary['completed'], counts=counts,
                          checks_passed=sum(check['passed'] for check in checks), checks_total=len(checks),
                          observed=summary['observed'], fatal_error=summary.get('fatal_error')), indent=2))
    return 0 if summary['passed'] else 2


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8085')
    parser.add_argument('--worker-endpoint', default='http://127.0.0.1:8769')
    parser.add_argument('--variant', required=True, choices=['baseline', 'candidate'])
    parser.add_argument('--jar', required=True, type=Path)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--environment-json', type=Path)
    parser.add_argument('--phase-seconds', type=float, default=60)
    parser.add_argument('--under-rate', type=float, default=2)
    parser.add_argument('--over-rate', type=float, default=20)
    parser.add_argument('--recovery-rate', type=float, default=2)
    parser.add_argument('--workspaces', type=int, default=4)
    parser.add_argument('--worker-delay-ms', type=float, default=400)
    parser.add_argument('--global-limit', type=int, default=12)
    parser.add_argument('--workspace-limit', type=int, default=8)
    parser.add_argument('--max-in-flight', type=int, default=32)
    parser.add_argument('--max-requests', type=int, default=10000)
    parser.add_argument('--http-timeout', type=float, default=5)
    parser.add_argument('--drain-timeout', type=float, default=90)
    parser.add_argument('--max-lateness-ms', type=float, default=100)
    parser.add_argument('--sample-interval', type=float, default=.25)
    parser.add_argument('--metrics-interval', type=float, default=5)
    return parser


def validate_args(args, parser):
    constraints = [(args.phase_seconds, 1, 180), (args.under_rate, .1, 100), (args.over_rate, .1, 100),
        (args.recovery_rate, .1, 100), (args.workspaces, 2, 8), (args.worker_delay_ms, 1, 5000),
        (args.max_in_flight, 1, 64), (args.max_requests, 1, 10000), (args.http_timeout, .1, 15),
        (args.drain_timeout, 1, 180), (args.max_lateness_ms, 1, 1000), (args.sample_interval, .1, 5),
        (args.metrics_interval, 1, 30), (args.global_limit, 1, 1000), (args.workspace_limit, 1, 1000)]
    if any(not math.isfinite(value) or not low <= value <= high for value, low, high in constraints):
        parser.error('Argument outside documented safety bounds')
    return args


def main():
    parser = build_parser()
    return asyncio.run(run(validate_args(parser.parse_args(), parser)))


if __name__ == '__main__':
    sys.exit(main())
