"""Frozen cross-snapshot contracts; default oracle execution makes zero model calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from lablens.datasets import DatasetBundle
from lablens.guided_planning import GUIDED_SYSTEM, draft_guided
from lablens.model import HTTPModel
from lablens.planning import PlanRequest, execute_plan, preview_plan, verify_plan

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / 'evaluation/v0.8/cases.json'
WARNING = ('Synthetic engineering fixtures, not experiment measurements. contract_pass checks only the '
           'predeclared machine-readable contract; not independent human review or general semantic '
           'correctness. Receipt recomputation is separate. A/B share IDs but have different values. '
           'Each run has fresh context, not conversational memory. Live plans are executed for evaluation '
           'without human approval. No failed draw is replaced. Concurrency affects latency.')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def load_cases():
    return json.loads(CASES.read_text())


def load_bundle(case):
    return json.loads((ROOT / case['snapshot_file']).read_text())


def snapshot_digest(bundle):
    """Independent canonical encoding, not the runtime fingerprint function."""
    normalized = {key: sorted(bundle[key], key=lambda row: row['id']) for key in ('runs', 'notes')}
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return 'snapshot:' + hashlib.sha256(raw.encode()).hexdigest()


def oracle_plan(case):
    """Reference plan from the literal contract; expected values are never computed here."""
    steps = []
    for i, item in enumerate(case['expected_steps'], 1):
        step = dict(id=f's{i}', kind=item['kind'], request_excerpt=item['request_excerpt'])
        if item['kind'] == 'query':
            step['query'] = deepcopy(item['parameters'])
        elif item['kind'] == 'compare':
            step.update(baseline={'run_id': item['baseline_id']}, candidate={'run_id': item['candidate_id']})
        steps.append(step)
    return dict(steps=steps, unhandled=[])


def grade_contract(case, bundle, trace):
    """Check literal expectations and current payloads, independently of receipt recomputation."""
    failures = []
    expected_snapshot = case['expected_snapshot_id']

    def require(ok, label):
        if not ok:
            failures.append(label)

    def near(actual, expected):
        return (type(actual) in (int, float) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9))

    def equal(actual, expected):
        if type(expected) is float:
            return near(actual, expected)
        # bool and int must not compare equal accidentally.
        return type(actual) is type(expected) and actual == expected

    def same_bundle(actual):
        try:
            return snapshot_digest(actual) == expected_snapshot and actual == bundle
        except (KeyError, TypeError, ValueError):
            return False

    require(snapshot_digest(bundle) == expected_snapshot, 'fixture fingerprint differs from declaration')
    require(trace.get('question') == case['question'], 'question differs from declaration')
    require(trace.get('snapshot_id') == expected_snapshot, 'trace uses wrong snapshot')
    require(same_bundle(trace.get('input_dataset')), 'trace input dataset is not current fixture')
    require(trace.get('preview', {}).get('snapshot_id') == expected_snapshot, 'preview uses wrong snapshot')
    require(trace.get('status') == 'completed', 'trace not completed')
    execution = trace.get('execution', {})
    require(execution.get('status') == 'completed', 'execution not completed')
    require(execution.get('unhandled') == [], 'execution has unhandled requests')
    plan = trace.get('plan', {})
    require(plan.get('unhandled') == [], 'plan has unhandled requests')
    receipt = execution.get('receipt', {})
    require(receipt.get('snapshot_id') == expected_snapshot, 'receipt uses wrong snapshot')
    require(same_bundle(receipt.get('dataset')), 'receipt embeds wrong snapshot data')
    require(receipt.get('plan') == plan, 'receipt and trace plans differ')
    require(receipt.get('results') == execution.get('results'), 'receipt and execution results differ')
    steps = plan.get('steps', [])
    results = execution.get('results', [])
    expectations = case['expected_steps']
    require(len(steps) == len(expectations), 'plan step count differs from contract')
    require(len(results) == len(expectations), 'result count differs from contract')
    require(execution.get('executed_step_ids') == [s.get('id') for s in steps], 'executed step IDs differ')
    rows = {r['id']: r for r in bundle['runs']}
    notes = {n['id']: n for n in bundle['notes']}
    for expected in expectations:
        label = expected['kind'] + ': ' + expected['request_excerpt']
        found = [s for s in steps if s.get('kind') == expected['kind']
                 and s.get('request_excerpt') == expected['request_excerpt']]
        require(len(found) == 1, label + ': expected exactly one corresponding step')
        if len(found) != 1:
            continue
        step = found[0]
        found_results = [r for r in results if r.get('id') == step['id']
                         and r.get('kind') == step['kind']
                         and r.get('request_excerpt') == step['request_excerpt']]
        require(len(found_results) == 1, label + ': result missing or duplicated')
        if len(found_results) != 1:
            continue
        result = found_results[0].get('result', {})
        if expected['kind'] == 'query':
            require(result.get('snapshot_id') == expected_snapshot, label + ': result snapshot mismatch')
            for key, value in expected['parameters'].items():
                require(equal(step.get('query', {}).get(key), value), label + ': plan parameter ' + key)
                require(equal(result.get('parameters', {}).get(key), value), label + ': result parameter ' + key)
            ids = result.get('ordered_run_ids', [])
            expected_ids = expected['ordered_run_ids']
            require(ids == expected_ids if expected['ordered'] else sorted(ids) == sorted(expected_ids),
                    label + ': selected IDs or order differ')
            require(result.get('runs') == [rows[rid] for rid in ids] if all(rid in rows for rid in ids) else False,
                    label + ': returned row values do not match current snapshot')
        elif expected['kind'] == 'compare':
            for role in ('baseline', 'candidate'):
                rid = expected[role + '_id']
                require(step.get(role, {}).get('run_id') == rid and step.get(role, {}).get('query_step') is None,
                        label + ': wrong explicit ' + role)
                require(result.get(role) == rows[rid], label + ': wrong ' + role + ' row values')
            for metric in ('additional_correct', 'accuracy_delta_percentage_points', 'length_reduction_percent'):
                require(near(result.get(metric), expected[metric]), label + ': wrong ' + metric)
        else:
            require(set(expected['required_fields']) <= set(result.get('fields', [])), label + ': required fields absent')
            require(set(result.get('missing_fields', [])) == set(expected['missing_fields']), label + ': wrong missing fields')
            require(sorted(result.get('notes', []), key=lambda n: n['id']) == sorted(notes.values(), key=lambda n: n['id']),
                    label + ': current verbatim notes missing or stale')
            require(set(expected['note_ids']) <= set(result.get('source_ids', [])), label + ': note references missing')
    return dict(contract_pass=not failures, failures=failures,
                scope='Predeclared synthetic contract only; not independent human or general semantic review')


def receipt_check(trace):
    receipt = trace.get('execution', {}).get('receipt')
    if receipt is None:
        return dict(status='not_available', verified=False)
    try:
        return dict(**verify_plan(receipt), verified=True)
    except (ValueError, TypeError, KeyError) as exc:
        return dict(status='failed', verified=False, error=str(exc)[:1000])


def summarize(case, trace, repeat):
    events = trace.get('events', [])
    usages = [e.get('usage') or {} for e in events if e.get('type') == 'model']
    calls = trace.get('model_calls', 0)
    known = calls == 0 or (len(usages) == calls and all(
        all(type(u.get(k)) in (int, float) for k in ('prompt_tokens', 'completion_tokens', 'estimated_cost'))
        for u in usages))
    return dict(case_id=case['id'], repeat=repeat, snapshot_id=case['expected_snapshot_id'],
                status=trace['status'], contract_pass=trace['contract']['contract_pass'],
                contract_failures=trace['contract']['failures'], receipt_verified=trace['receipt_check']['verified'],
                model_calls=calls, repair_calls=max(0, calls - 1),
                latency_seconds=trace.get('latency_seconds', 0),
                reported_prompt_tokens=sum(u.get('prompt_tokens', 0) for u in usages),
                reported_completion_tokens=sum(u.get('completion_tokens', 0) for u in usages),
                reported_estimated_cost_usd=sum(u.get('estimated_cost', 0) for u in usages) if known else None,
                usage_incomplete=bool(trace.get('usage_incomplete')) or not known,
                manual_task_success=None)


def execute_case(case, repeat=1, *, live=False, bundle=None):
    bundle = deepcopy(bundle) if bundle is not None else load_bundle(case)
    started = time.monotonic()
    trace = dict(question=case['question'], snapshot_id=case['expected_snapshot_id'], input_dataset=bundle,
                 status='harness_error', events=[], model_calls=0, usage_incomplete=False)
    try:
        if live:
            trace = draft_guided(HTTPModel(), case['question'], max_attempts=2,
                                 check_request_rules=True, dataset=DatasetBundle.model_validate(bundle))
        else:
            trace.update(mode='offline_oracle', plan=oracle_plan(case), status='needs_review')
            reference = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle)
            trace['plan'] = reference.plan.model_dump()
            trace['preview'] = preview_plan(reference)
        if trace['status'] == 'needs_review':
            request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle,
                                  preview_id=trace['preview']['preview_id'])
            trace['execution'] = execute_plan(request)
            trace['status'] = trace['execution']['status']
    except Exception as exc:  # One local/provider failure must not discard other draws.
        trace.update(status='harness_error', error=f'{type(exc).__name__}: {str(exc)[:1000]}')
    trace.update(case_id=case['id'], repeat=repeat, review_mode='unreviewed_evaluation' if live else 'predeclared_oracle',
                 latency_seconds=round(time.monotonic() - started, 3))
    try:
        trace['contract'] = grade_contract(case, bundle, trace)
    except (ValueError, TypeError, KeyError) as exc:
        trace['contract'] = dict(contract_pass=False, failures=[f'Invalid trace for contract: {type(exc).__name__}'])
    trace['receipt_check'] = receipt_check(trace)
    return trace


def freeze_sources(out, *, live, repeats, workers):
    files = sorted(set(list((ROOT / 'lablens').glob('*.py'))
                       + list((ROOT / 'lablens/data').glob('*.json'))
                       + list((ROOT / 'evaluation/v0.8').rglob('*.json'))
                       + [ROOT / 'evaluation/v0.8/rubric.md', Path(__file__).resolve()]))
    hashes = {}
    for source in files:
        relative = source.relative_to(ROOT)
        data = source.read_bytes()
        target = out / 'frozen-source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), mode='live' if live else 'offline_oracle',
                    git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    source_sha256=hashes,
                    cases=json.loads((out / 'frozen-source/evaluation/v0.8/cases.json').read_text()),
                    repeats=repeats, workers=workers,
                    max_model_requests_per_draw=2 if live else 0, prompt=GUIDED_SYSTEM,
                    prompt_sha256=hashlib.sha256(GUIDED_SYSTEM.encode()).hexdigest(),
                    warning=WARNING, raw_provider_response_scope='Message and usage returned by HTTPModel; HTTP headers and provider body envelope are not retained')
    save(out / 'manifest.json', manifest)
    return manifest


def aggregate(rows):
    return dict(draws=len(rows), contract_pass=sum(r['contract_pass'] for r in rows),
                receipts_verified=sum(r['receipt_verified'] for r in rows),
                model_calls=sum(r['model_calls'] for r in rows), repair_calls=sum(r['repair_calls'] for r in rows),
                reported_prompt_tokens=sum(r['reported_prompt_tokens'] for r in rows),
                reported_completion_tokens=sum(r['reported_completion_tokens'] for r in rows),
                known_reported_estimated_cost_usd=sum(r['reported_estimated_cost_usd'] or 0 for r in rows),
                unknown_usage_draws=[f"{r['case_id']}-r{r['repeat']}" for r in rows if r['usage_incomplete']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly authorize bounded real model calls')
    parser.add_argument('--repeats', type=int, choices=(1, 2), default=1)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports')
    args = parser.parse_args()
    out = args.output / datetime.now(timezone.utc).strftime('snapshots-%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    manifest = freeze_sources(out, live=args.live, repeats=args.repeats, workers=args.workers)
    if args.live:
        # No HTTPModel object exists before all source bytes and contracts are persisted.
        try:
            model = HTTPModel()
            manifest.update(model=model.model, temperature=0, max_tokens=2600,
                            request_timeout_seconds=model.timeout_seconds,
                            model_revision='Provider slug only; backend revision unavailable')
        except Exception as exc:
            manifest['configuration_error'] = f'{type(exc).__name__}: {str(exc)[:1000]}'
        save(out / 'manifest.json', manifest)
    print(out, flush=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(execute_case, case, repeat, live=args.live,
                               bundle=json.loads((out / 'frozen-source' / case['snapshot_file']).read_text())): (case, repeat)
                   for case in manifest['cases'] for repeat in range(1, args.repeats + 1)}
        for future in as_completed(futures):
            case, repeat = futures[future]
            trace = future.result()
            save(out / f"{case['id']}-r{repeat}.json", trace)
            rows.append(summarize(case, trace, repeat))
            ordered = sorted(rows, key=lambda row: (row['case_id'], row['repeat']))
            save(out / 'summary.json', dict(mode=manifest['mode'], cases=ordered,
                                           aggregate=aggregate(rows), warning=WARNING))
            print(case['id'], f'r{repeat}', trace['status'],
                  'contract_pass=' + str(trace['contract']['contract_pass']), flush=True)
    print(json.dumps(aggregate(rows), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
