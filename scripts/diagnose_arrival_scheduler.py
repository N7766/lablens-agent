#!/usr/bin/env python3
"""Bounded local scheduler comparison; no HTTP, providers, databases or services.

Synthetic dispatch is asyncio.sleep. Observer blocking is explicitly injected.
This cannot identify the cause of a historical missed request.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import platform
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MODES = ('shared_loop', 'isolated_process')
SOURCE_FILES = ('scripts/diagnose_arrival_scheduler.py',
                'docs/evaluations/conversation-round14/sender-protocol.md',
                'tests/test_arrival_scheduler.py')


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def write_rows(path, rows):
    with path.open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def planned_slots(phase_seconds):
    slots = []
    for index, (stage, rate) in enumerate(zip(('under', 'over', 'recovery'), (2, 20, 2))):
        for number in range(phase_seconds * rate):
            slots.append(dict(sequence=len(slots)+1, stage=stage,
                              scheduled_offset_s=round(index*phase_seconds+number/rate, 9)))
    return slots


def planned_observations(duration):
    return [dict(sequence=index+1, scheduled_offset_s=round(.2+index, 9))
            for index in range(duration) if .2+index < duration]


def decision(lateness_ms, in_flight, maximum=32):
    if lateness_ms > 100:
        return 'scheduler_late'
    if in_flight >= maximum:
        return 'client_in_flight_limit'
    return None


def distribution(values):
    values = sorted(x for x in values if type(x) in (float, int) and math.isfinite(x))
    def quantile(q):
        if not values:
            return None
        index = (len(values)-1)*q
        lower, upper = math.floor(index), math.ceil(index)
        return round(values[lower]+(values[upper]-values[lower])*(index-lower), 3)
    return dict(n=len(values), p50_ms=quantile(.5), p95_ms=quantile(.95),
                p99_ms=quantile(.99), max_ms=max(values) if values else None)


def observe_once(slot, started, args, events, payloads):
    began = time.monotonic()
    events.write(json.dumps(dict(event='started', **slot, started_offset_s=began-started,
                                 pid=os.getpid()))+'\n')
    events.flush()
    time.sleep(args['blocking_ms']/1000)
    # Actual JSON encoding and file writes happen on the selected observer.
    encoded = json.dumps(dict(sequence=slot['sequence'], synthetic='x'*args['payload_bytes']))
    payloads.write(encoded+'\n')
    payloads.flush()
    finished = time.monotonic()
    events.write(json.dumps(dict(event='completed', sequence=slot['sequence'],
        finished_offset_s=finished-started, duration_ms=(finished-began)*1000,
        encoded_bytes=len(encoded.encode()), pid=os.getpid()))+'\n')
    events.flush()


def observer_child(directory, args, connection, stop):
    """Spawn-safe entry point; no inherited provider or application objects."""
    directory = Path(directory)
    try:
        with (directory/'observer-events.jsonl').open('x', buffering=1) as events, \
             (directory/'observer-payloads.jsonl').open('x', buffering=1) as payloads:
            connection.send({'ready': True, 'pid': os.getpid()})
            if not connection.poll(5):
                raise TimeoutError('Start handshake was not received')
            started = connection.recv()['started']
            for slot in planned_observations(args['duration']):
                delay = max(0, started+slot['scheduled_offset_s']-time.monotonic())
                if stop.wait(delay):
                    break
                observe_once(slot, started, args, events, payloads)
    finally:
        connection.close()


def stop_child(process, stop):
    """Bounded cleanup; report every escalation and whether exit was confirmed."""
    stop.set()
    actions, began = [], time.monotonic()
    process.join(2)
    if process.is_alive():
        actions.append('terminate')
        process.terminate()
        process.join(1)
    if process.is_alive():
        actions.append('kill')
        process.kill()
        process.join(1)
    result = dict(confirmed_exited=not process.is_alive(), exitcode=process.exitcode,
                  escalation=actions, elapsed_ms=(time.monotonic()-began)*1000)
    if result['confirmed_exited']:
        process.close()
    return result


def read_events(path):
    records, invalid = [], 0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
                records.append(value)
            except (ValueError, TypeError):
                invalid += 1
    return records, invalid


def observer_ledger(planned, events):
    rows = []
    for slot in planned:
        start = [x for x in events if x.get('sequence') == slot['sequence'] and x.get('event') == 'started']
        end = [x for x in events if x.get('sequence') == slot['sequence'] and x.get('event') == 'completed']
        row = dict(slot, start_records=len(start), completion_records=len(end))
        if (len(start) == len(end) == 1
                and all(type(start[0].get(key)) in (int, float) and math.isfinite(start[0][key])
                        for key in ('started_offset_s',))
                and all(type(end[0].get(key)) in (int, float) and math.isfinite(end[0][key])
                        for key in ('finished_offset_s', 'duration_ms'))
                and end[0]['finished_offset_s'] >= start[0]['started_offset_s']
                and end[0]['duration_ms'] >= 0):
            row.update(status='completed', started_offset_s=start[0]['started_offset_s'],
                       finished_offset_s=end[0]['finished_offset_s'], duration_ms=end[0]['duration_ms'],
                       lateness_ms=(start[0]['started_offset_s']-slot['scheduled_offset_s'])*1000)
        else:
            row['status'] = ('start_not_observed' if not start and not end else
                             'completion_not_observed' if len(start) == 1 and not end else 'invalid_event_record')
        rows.append(row)
    return rows


async def run_variant(mode, args, directory):
    directory.mkdir()
    config = dict(duration=args.phase_seconds*3, blocking_ms=args.blocking_ms,
                  payload_bytes=args.payload_bytes)
    slots, observations = planned_slots(args.phase_seconds), planned_observations(config['duration'])
    rows, lag, pending = [], [], set()
    child = child_stop = connection = shared_task = heartbeat_task = None
    lifecycle = dict(mode=mode, confirmed_exited=True, exitcode=None, escalation=[])
    error = None
    started = None
    peak = 0
    try:
        if mode == 'isolated_process':
            context = multiprocessing.get_context('spawn')
            connection, remote = context.Pipe()
            child_stop = context.Event()
            child = context.Process(target=observer_child, args=(str(directory), config, remote, child_stop), daemon=True)
            child.start()
            remote.close()
            if not connection.poll(5):
                raise TimeoutError('Observer readiness budget exhausted')
            ready = connection.recv()
            if ready.get('ready') is not True:
                raise RuntimeError('Observer did not acknowledge readiness')
            lifecycle.update(ready)
        started = time.monotonic()+.1
        if connection:
            connection.send({'started': started})

        async def shared_observer():
            with (directory/'observer-events.jsonl').open('x', buffering=1) as events, \
                 (directory/'observer-payloads.jsonl').open('x', buffering=1) as payloads:
                for slot in observations:
                    await asyncio.sleep(max(0, started+slot['scheduled_offset_s']-time.monotonic()))
                    observe_once(slot, started, config, events, payloads)

        async def heartbeat():
            tick, count = 0, config['duration']*100
            while tick < count:
                target = started+tick*.01
                await asyncio.sleep(max(0, target-time.monotonic()))
                actual = time.monotonic()
                skipped = min(count-tick-1, max(0, math.floor((actual-target)/.01)))
                lag.append(dict(tick=tick, scheduled_offset_s=round(tick*.01, 9),
                                observed_offset_s=actual-started, lag_ms=max(0, (actual-target)*1000),
                                skipped_ticks=skipped))
                tick += 1+skipped

        async def simulated_dispatch(row):
            try:
                await asyncio.sleep(.002)
                row.update(outcome='dispatched', completion='local_simulation_completed',
                           completed_offset_s=time.monotonic()-started)
            except BaseException as exc:
                row.update(outcome='dispatch_error', error_type=type(exc).__name__)
            finally:
                rows.append(row)

        if mode == 'shared_loop':
            shared_task = asyncio.create_task(shared_observer())
        heartbeat_task = asyncio.create_task(heartbeat())
        for slot in slots:
            await asyncio.sleep(max(0, started+slot['scheduled_offset_s']-time.monotonic()))
            actual = time.monotonic()-started
            lateness = max(0, (actual-slot['scheduled_offset_s'])*1000)
            reason = decision(lateness, len(pending))
            row = dict(slot, lateness_ms=lateness, dispatch_offset_s=None)
            if reason:
                rows.append(dict(row, outcome='unsent', unsent_reason=reason))
                continue
            row['dispatch_offset_s'] = actual
            task = asyncio.create_task(simulated_dispatch(row))
            pending.add(task)
            task.add_done_callback(pending.discard)
            peak = max(peak, len(pending))
        await asyncio.sleep(max(0, started+config['duration']-time.monotonic()))
        if pending:
            await asyncio.gather(*pending)
        if shared_task:
            await shared_task
        await heartbeat_task
    except BaseException as exc:
        error = type(exc).__name__
    finally:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in (shared_task, heartbeat_task):
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if child and child.pid is not None:
            lifecycle.update(stop_child(child, child_stop))
        if connection:
            connection.close()
        recorded = {row['sequence'] for row in rows}
        rows.extend(dict(slot, outcome='unsent', unsent_reason='run_aborted', dispatch_offset_s=None)
                    for slot in slots if slot['sequence'] not in recorded)
        rows.sort(key=lambda row: row['sequence'])
        events_path = directory/'observer-events.jsonl'
        events, invalid_events = read_events(events_path)
        observed = observer_ledger(observations, events)
        write_rows(directory/'requests.jsonl', rows)
        write_rows(directory/'heartbeat.jsonl', lag)
        write_rows(directory/'observers.jsonl', observed)
        counts = dict(Counter(row['outcome'] for row in rows))
        complete_observers = sum(row['status'] == 'completed' for row in observed)
        summary = dict(mode=mode, run_error_type=error, counts=counts, planned_slots=len(slots),
            unsent_reasons=dict(Counter(row['unsent_reason'] for row in rows if row['outcome']=='unsent')),
            lifecycle=lifecycle, max_in_flight=peak, planned_observers=len(observations),
            complete_observers=complete_observers, invalid_observer_lines=invalid_events,
            loop_lag=distribution([row['lag_ms'] for row in lag]),
            sender_lateness=distribution([row.get('lateness_ms') for row in rows]),
            observer_duration=distribution([row.get('duration_ms') for row in observed]),
            heartbeat_planned_ticks=config['duration']*100,
            heartbeat_accounted_ticks=sum(1+row['skipped_ticks'] for row in lag),
            actual_window_and_cleanup_seconds=time.monotonic()-started if started else None,
            accounting_valid=(len(rows) == len(slots) == len({row['sequence'] for row in rows})
                and counts.get('dispatch_error', 0) == 0 and complete_observers == len(observations)
                and peak<=32 and sum(1+row['skipped_ticks'] for row in lag)==config['duration']*100
                and len(events)==len(observations)*2 and invalid_events==0
                and lifecycle['confirmed_exited'] and not lifecycle.get('escalation') and error is None
                and (mode=='shared_loop' or lifecycle.get('exitcode')==0)))
        write_new(directory/'summary.json', summary)
    return summary


def comparison(shared, isolated, source_unchanged):
    checks = [dict(name='Both variant ledgers and observer lifecycle are complete',
                   passed=all(item['accounting_valid'] for item in (shared, isolated))),
              dict(name='Frozen source and protocol unchanged', passed=source_unchanged),
              dict(name='Shared-loop injection produces missed slots and lag above 100ms',
                   passed=shared['unsent_reasons'].get('scheduler_late', 0)>0 and (shared['loop_lag']['max_ms'] or 0)>100),
              dict(name='Isolated observer has zero missed slots and lag at most 100ms',
                   passed=isolated['counts'].get('unsent', 0)==0 and isolated['loop_lag']['n']>0
                   and isolated['loop_lag']['max_ms']<=100)]
    return dict(mechanism_comparison_passed=all(x['passed'] for x in checks), checks=checks,
                scope='Controlled blocking observer mechanism only; no historical root cause or business capacity claim',
                historical_round13_acceptance='failed_unchanged')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--phase-seconds', type=int, default=6)
    parser.add_argument('--blocking-ms', type=float, default=180)
    parser.add_argument('--payload-bytes', type=int, default=32768)
    args = parser.parse_args(argv)
    if (not 1<=args.phase_seconds<=8 or not math.isfinite(args.blocking_ms)
            or not 1<=args.blocking_ms<=300 or not 1<=args.payload_bytes<=65536):
        parser.error('Outside bounded local experiment limits')
    return args


def main(argv=None):
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    before = hashes()
    write_new(args.output/'manifest.json', dict(run_id=str(uuid4()), source_commit=args.source_commit,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        source_hashes=before, python=platform.python_version(), platform=platform.platform(),
        parameters={k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
        rates=[2,20,2], max_lateness_ms=100, modes=list(MODES),
        synthetic_dispatch_seconds=.002, observation_first_offset_seconds=.2,
        observation_interval_seconds=1, heartbeat_interval_seconds=.01,
        scope='No network, provider, database or business service calls'))
    variants = []
    for mode in MODES:
        variants.append(asyncio.run(run_variant(mode, args, args.output/mode)))
        if variants[-1]['run_error_type'] is not None:
            break
    after = hashes()
    result = (comparison(*variants, before==after) if len(variants)==2 else
              dict(mechanism_comparison_passed=False, not_run_modes=list(MODES[len(variants):]),
                   reason='A variant failed or was interrupted; no following variant started',
                   historical_round13_acceptance='failed_unchanged'))
    result.update(variants=variants, source_hashes_after=after)
    write_new(args.output/'summary.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['mechanism_comparison_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
