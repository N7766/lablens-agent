#!/usr/bin/env python3
"""Nine-minute, zero-provider load with bounded resource observations.

Reuses admission scheduling/accounting. No service startup, retries or cleanup.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('admission_base', ROOT/'scripts/evaluate_admission_load.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

METRICS = {name: name for name in (
    'process.cpu.usage', 'system.cpu.usage', 'jvm.memory.used', 'jvm.memory.max',
    'hikaricp.connections.active', 'hikaricp.connections.idle', 'hikaricp.connections.pending',
    'hikaricp.connections.max', 'hikaricp.connections.acquire', 'hikaricp.connections.timeout',
    'tomcat.threads.busy', 'tomcat.threads.current', 'tomcat.threads.config.max',
    'lablens.admission.guard.wait', 'lablens.admission.guard.inflight', 'lablens.admission.transaction')}
METRICS.update({'jvm.heap.used': 'jvm.memory.used?tag=area:heap',
                'jvm.heap.max': 'jvm.memory.max?tag=area:heap'})
for name in ('active', 'queued', 'pool.max', 'queue.capacity'):
    for executor in ('worker', 'dispatch', 'cancellation'):
        METRICS[f'executor.{executor}.{name}'] = f'lablens.executor.{name}?tag=executor:{executor}'


def metric_summary(samples, name):
    observations = [sample['actuator'][name] for sample in samples if name in sample.get('actuator', {})]
    statistics = {}
    for row in observations:
        body = row.get('body')
        if row.get('http_status') != 200 or not isinstance(body, dict):
            continue
        for measurement in body.get('measurements', []):
            value, statistic = measurement.get('value'), measurement.get('statistic')
            if type(value) in (int, float) and math.isfinite(value) and isinstance(statistic, str):
                statistics.setdefault(statistic, []).append(value)
    return {'http_status_counts': dict(Counter(str(row.get('http_status')) for row in observations)),
            'availability': 'observed' if statistics else 'unavailable',
            'statistics': {key: {'n': len(values), 'first': values[0], 'last': values[-1],
                                'min': min(values), 'max': max(values)} for key, values in statistics.items()}}


def no_observed_timeout(metric):
    count = metric["statistics"].get("COUNT", {})
    return count.get("n", 0) > 0 and count.get("max") == 0


def review(directory, args):
    summary = json.loads((directory/'summary.json').read_text())
    samples = [json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
    requests = [json.loads(line) for line in (directory/'requests.jsonl').read_text().splitlines()]
    tasks = [json.loads(line) for line in (directory/'tasks.jsonl').read_text().splitlines()]
    metrics = {name: metric_summary(samples, name) for name in METRICS}
    checks = []
    def check(name, passed, evidence=None):
        checks.append({'name': name, 'passed': bool(passed), 'evidence': evidence})
    check('Admission accounting and terminal acceptance passed', summary['passed'])
    check('Exactly the frozen nine-minute schedule ran', args.phase_seconds == 180
          and len(requests) == 4320 and args.under_rate == 2 and args.over_rate == 20 and args.recovery_rate == 2)
    required = ['hikaricp.connections.active', 'hikaricp.connections.pending', 'hikaricp.connections.max',
                'hikaricp.connections.acquire', 'hikaricp.connections.timeout', 'tomcat.threads.busy',
                'tomcat.threads.config.max', 'jvm.heap.used', 'lablens.admission.guard.wait',
                'lablens.admission.transaction', 'executor.worker.active', 'executor.dispatch.queued']
    check('Required resource metrics have real samples', all(metrics[name]['availability'] == 'observed' for name in required),
          [name for name in required if metrics[name]['availability'] != 'observed'])
    status_samples = [x['system']['body'] for x in samples if x['system'].get('http_status') == 200]
    check('System status samples are all available', len(status_samples) == len(samples) > 0)
    acquired = metrics['hikaricp.connections.timeout']['statistics'].get('COUNT', {})
    check('No observed connection acquisition timeout', no_observed_timeout(metrics['hikaricp.connections.timeout']), acquired)
    after = summary.get('system_after', {})
    accepted = Counter(x['task_id'] for x in requests if x['outcome'] == 'accepted')
    terminal = Counter(x['task_id'] for x in tasks)
    check('Accepted IDs equal individually observed task IDs', accepted == terminal and bool(accepted) and all(x == 1 for x in accepted.values()))
    check('Database contains only the accepted tasks', sum(after.get('task_counts', {}).values()) == len(accepted)
          and after.get('task_counts', {}).get('SUCCEEDED') == len(accepted))
    resources = summary.get('system_before', {}).get('resources', {})
    pool = resources.get('database_pool', {})
    http = resources.get('http', {})
    check('Runtime declares the frozen connection and HTTP budgets',
          pool.get('max') == pool.get('min_idle') == 12 and pool.get('acquire_timeout_ms') == 3000
          and http.get('threads_max') == 200 and http.get('threads_min_spare') == 10
          and http.get('max_connections') == 8192 and http.get('accept_count') == 100, resources)
    for active_name, maximum_name in [('hikaricp.connections.active', 'hikaricp.connections.max'),
                                      ('tomcat.threads.busy', 'tomcat.threads.config.max')]:
        active_values = metrics[active_name]['statistics'].get('VALUE', {})
        maximum_values = metrics[maximum_name]['statistics'].get('VALUE', {})
        check(active_name+' samples within configured maximum',
              'max' in active_values and 'min' in maximum_values and active_values['max'] <= maximum_values['min'])
    for executor in ('worker', 'dispatch', 'cancellation'):
        for name, capacity in [('active', 'pool.max'), ('queued', 'queue.capacity')]:
            values = metrics[f'executor.{executor}.{name}']['statistics'].get('VALUE', {})
            limits = metrics[f'executor.{executor}.{capacity}']['statistics'].get('VALUE', {})
            check(f'{executor} {name} samples within configured budget',
                  'max' in values and 'min' in limits and values['max'] <= limits['min'])
    # Per-minute ledger includes refused arrivals; it is not successful-task-only throughput.
    windows = []
    for minute in range(9):
        rows = [x for x in requests if minute*60 <= x['scheduled_offset_s'] < (minute+1)*60]
        windows.append({'minute': minute+1, **base.stage_metrics(rows)})
    result = {'scope': '9-minute same-host synthetic fixed-delay BM25; not production SLA or model capacity',
        'passed': all(x['passed'] for x in checks), 'checks': checks, 'metrics': metrics,
        'per_minute_submission_ledger': windows,
        'source_run_id': summary['run_id'],
        'metric_semantics': {'gauges': 'Sampled values, not continuous extrema; observer contributes load',
            'COUNT_TOTAL_TIME': 'Cumulative timer counters, not percentiles; difference gives interval mean only',
            'MAX': 'Micrometer time-window maximum sampled over the experiment, not exact lifetime maximum',
            'jvm.heap': 'area:heap pools only; neither OS RSS nor process memory',
            'admission.guard.wait': 'Lock SQL call elapsed including client/server round trip, not pure server lock wait',
            'admission.transaction': 'After callback entry through transaction completion; excludes prior Redis and connection acquisition'},
        'limitations': ['One JVM, one HTTP generator; actual in-flight reported separately',
                        'Nine minutes does not establish multi-hour stability or absence of leaks',
                        'No performance gain inferred against shorter Round12 with different instrumentation',
                        'Worker log still requires independent per-ID reconciliation by operator']}
    base.dump(directory/'resource-summary.json', result)
    return result


def main():
    parser = base.build_parser()
    parser.description = __doc__
    parser.set_defaults(phase_seconds=180, sample_interval=1, metrics_interval=5)
    args = base.validate_args(parser.parse_args(), parser)
    if args.variant != 'candidate' or args.phase_seconds != 180 or (args.under_rate,args.over_rate,args.recovery_rate) != (2,20,2):
        parser.error('Round13 fixes candidate, 180 seconds per stage and rates 2/20/2')
    extra = (Path(__file__), ROOT/'docs/evaluations/conversation-round13/load-protocol.md',
             ROOT/'lablens/runtime_telemetry.py', ROOT/'lablens/model.py')
    code = asyncio.run(base.run(args, metrics=METRICS, extra_sources=extra))
    result = review(args.output, args)
    print(json.dumps({'resource_acceptance_passed': result['passed'], 'checks': len(result['checks'])}))
    return code if code else (0 if result['passed'] else 2)


if __name__ == '__main__':
    sys.exit(main())
