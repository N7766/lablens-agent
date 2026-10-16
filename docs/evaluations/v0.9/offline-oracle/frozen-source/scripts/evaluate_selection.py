"""Compare predeclared selection protocols; default execution is a zero-API oracle."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import time

from lablens.datasets import DatasetBundle
from lablens.guided_planning import (GUIDED_SYSTEM, GUIDED_SYSTEM_V2, Proposal, ProposalV2,
                                    draft_guided)
from lablens.model import HTTPModel
from lablens.planning import PlanRequest, execute_plan, preview_plan

ROOT = Path(__file__).resolve().parents[1]
# Explicit sibling import also works when this file is executed by path.
_spec = importlib.util.spec_from_file_location('selection_contracts', ROOT / 'scripts/evaluate_snapshots.py')
_contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contracts)
save = _contracts.save
snapshot_digest = _contracts.snapshot_digest
oracle_plan = _contracts.oracle_plan
grade_contract = _contracts.grade_contract
receipt_check = _contracts.receipt_check
WARNING = ('Synthetic engineering contracts, not measured experiment outcomes or independent human review. '
           'v1 and v2 are independent draws of different protocol packages (prompt, schema, compiler and '
           'version-specific repair guidance), with the same model, data and two-request budget. '
           'This small comparison does not isolate the cause of differences or establish general semantic '
           'accuracy. First-attempt scoring locally replays the already returned first message; it sends no '
           'additional provider request. All failures are retained. Receipt consistency is separate from '
           'contract success. Concurrency affects latency. Development regressions are separate from new fixtures.')


def load_cases(suite='holdout'):
    if suite not in {'holdout', 'dev'}:
        raise ValueError('Unknown suite')
    name = 'cases.json' if suite == 'holdout' else 'dev-cases.json'
    return json.loads((ROOT / 'evaluation/v0.9' / name).read_text())


def load_bundle(case):
    return json.loads((ROOT / case['snapshot_file']).read_text())


def execute_prepared(case, bundle, trace):
    if trace['status'] == 'needs_review':
        request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle,
                              preview_id=trace['preview']['preview_id'])
        trace['execution'] = execute_plan(request)
        trace['status'] = trace['execution']['status']
    return trace


class FirstReply:
    """One already-paid reply; replay is not a live request or another independent draw."""
    mode = 'recorded_first_reply'

    def __init__(self, message):
        self.message = deepcopy(message)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError('First-attempt scoring cannot request a second reply')
        return self.message, dict(prompt_tokens=0, completion_tokens=0, estimated_cost=0,
                                  source='replayed_not_billed')


def first_attempt_contract(case, bundle, trace, strategy):
    events = [event for event in trace.get('events', []) if event.get('type') == 'model']
    if not events:
        return dict(status='no_model_reply', contract_pass=False,
                    failures=['No returned first model message'], additional_provider_requests=0)
    try:
        replay = draft_guided(FirstReply(events[0]['message']), case['question'], max_attempts=1,
                              check_request_rules=True, dataset=DatasetBundle.model_validate(bundle),
                              proposal_version=strategy)
        execute_prepared(case, bundle, replay)
        grade = grade_contract(case, bundle, replay)
        return dict(status=replay['status'], **grade, additional_provider_requests=0,
                    method='Local replay of original first returned message; no additional model sampling')
    except Exception as exc:
        return dict(status='scoring_error', contract_pass=False,
                    failures=[f'{type(exc).__name__}: {str(exc)[:1000]}'], additional_provider_requests=0)


def execute_case(case, repeat=1, *, strategy='v2', live=False, bundle=None):
    if strategy not in {'v1', 'v2'}:
        raise ValueError('Unknown strategy')
    bundle = deepcopy(bundle) if bundle is not None else load_bundle(case)
    started = time.monotonic()
    trace = dict(question=case['question'], snapshot_id=case['expected_snapshot_id'], input_dataset=bundle,
                 status='harness_error', events=[], model_calls=0, usage_incomplete=False)
    try:
        if live:
            trace = draft_guided(HTTPModel(), case['question'], max_attempts=2,
                                 check_request_rules=True, dataset=DatasetBundle.model_validate(bundle),
                                 proposal_version=strategy)
        else:
            trace.update(mode='offline_oracle', plan=oracle_plan(case), status='needs_review')
            reference = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle)
            trace['plan'] = reference.plan.model_dump()
            trace['preview'] = preview_plan(reference)
        execute_prepared(case, bundle, trace)
    except Exception as exc:  # One failed draw cannot discard other trials.
        trace.update(status='harness_error', error=f'{type(exc).__name__}: {str(exc)[:1000]}')
    # Exclude local first-reply rescoring from measured end-to-end latency.
    trace.update(case_id=case['id'], repeat=repeat, strategy=strategy,
                 review_mode='unreviewed_evaluation' if live else 'predeclared_oracle',
                 latency_seconds=round(time.monotonic() - started, 3))
    try:
        trace['contract'] = grade_contract(case, bundle, trace)
    except (ValueError, TypeError, KeyError) as exc:
        trace['contract'] = dict(contract_pass=False, failures=[f'Invalid trace: {type(exc).__name__}'])
    trace['receipt_check'] = receipt_check(trace)
    trace['first_attempt_contract'] = (first_attempt_contract(case, bundle, trace, strategy) if live else
        dict(status='not_applicable_offline_oracle', contract_pass=None, additional_provider_requests=0))
    return trace


def freeze_sources(out, *, live, repeats, workers, strategies=('v1', 'v2'), suite='holdout'):
    files = sorted(set(list((ROOT / 'lablens').glob('*.py'))
                       + list((ROOT / 'lablens/data').glob('*.json'))
                       + list((ROOT / 'evaluation/v0.9').rglob('*'))
                       + list((ROOT / 'evaluation/v0.8/fixtures').glob('*.json'))
                       + [ROOT / 'scripts/evaluate_snapshots.py', Path(__file__).resolve()]))
    hashes = {}
    for source in files:
        if not source.is_file():
            continue
        relative = source.relative_to(ROOT)
        data = source.read_bytes()
        target = out / 'frozen-source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    protocols = {}
    for version, prompt, schema in [('v1', GUIDED_SYSTEM, Proposal.model_json_schema()),
                                    ('v2', GUIDED_SYSTEM_V2, ProposalV2.model_json_schema())]:
        prompt_name = f'{version}-system-prompt.txt'
        schema_name = f'{version}-proposal-schema.json'
        (out / prompt_name).write_text(prompt)
        save(out / schema_name, schema)
        protocols[version] = dict(prompt_file=prompt_name, schema_file=schema_name,
                                  prompt_sha256=hashlib.sha256((out / prompt_name).read_bytes()).hexdigest(),
                                  schema_sha256=hashlib.sha256((out / schema_name).read_bytes()).hexdigest())
    case_name = 'cases.json' if suite == 'holdout' else 'dev-cases.json'
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), mode='live' if live else 'offline_oracle',
                    suite=suite, git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    source_sha256=hashes, protocols=protocols,
                    cases=json.loads((out / 'frozen-source/evaluation/v0.9' / case_name).read_text()),
                    strategies=list(strategies), repeats=repeats, workers=workers,
                    max_model_requests_per_draw=2 if live else 0,
                    request_rules_enabled=True, warning=WARNING,
                    raw_provider_response_scope='Message and usage returned by HTTPModel; HTTP headers and provider body envelope are not retained')
    save(out / 'manifest.json', manifest)
    return manifest


def summarize(case, trace, repeat):
    row = _contracts.summarize(case, trace, repeat)
    row.update(strategy=trace['strategy'],
               first_contract_pass=trace['first_attempt_contract']['contract_pass'],
               request_conflicts=sum(e.get('type') == 'request_check' and e.get('report', {}).get('has_conflicts', False)
                                     for e in trace.get('events', [])),
               raw_model_replies=sum(e.get('type') == 'model' for e in trace.get('events', [])))
    return row


def aggregate(rows):
    result = _contracts.aggregate(rows)
    result.update(first_contract_pass=sum(r['first_contract_pass'] is True for r in rows),
                  first_contract_assessed=sum(r['first_contract_pass'] is not None for r in rows),
                  repair_runs=sum(r['repair_calls'] > 0 for r in rows),
                  repaired_contract_success=sum(r['repair_calls'] > 0 and r['contract_pass'] for r in rows),
                  request_conflicts=sum(r['request_conflicts'] for r in rows),
                  median_latency_seconds=statistics.median(r['latency_seconds'] for r in rows) if rows else None)
    # Include strategy in labels to avoid collision between independent v1/v2 draws.
    result['unknown_usage_draws'] = [f"{r['strategy']}-{r['case_id']}-r{r['repeat']}" for r in rows if r['usage_incomplete']]
    return result


def summary(rows, manifest):
    return dict(mode=manifest['mode'], suite=manifest['suite'],
                cases=sorted(rows, key=lambda row: (row['strategy'], row['case_id'], row['repeat'])),
                strategies={version: aggregate([r for r in rows if r['strategy'] == version])
                            for version in manifest['strategies']},
                aggregate=aggregate(rows), warning=WARNING)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable bounded real model calls')
    parser.add_argument('--suite', choices=('holdout', 'dev'), default='holdout')
    parser.add_argument('--strategies', choices=('both', 'v2'), default='both')
    parser.add_argument('--repeats', type=int, choices=(1, 2), default=1)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports')
    args = parser.parse_args()
    strategies = ('v1', 'v2') if args.strategies == 'both' else ('v2',)
    out = args.output / datetime.now(timezone.utc).strftime('selection-%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    manifest = freeze_sources(out, live=args.live, repeats=args.repeats, workers=args.workers,
                              strategies=strategies, suite=args.suite)
    if args.live:
        # Freeze all prompt, schema, source, contracts and fixture bytes before constructing HTTPModel.
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
        futures = {pool.submit(execute_case, case, repeat, strategy=strategy, live=args.live,
                               bundle=json.loads((out / 'frozen-source' / case['snapshot_file']).read_text())):
                   (strategy, case, repeat)
                   for case in manifest['cases'] for repeat in range(1, args.repeats + 1)
                   for strategy in strategies}
        for future in as_completed(futures):
            strategy, case, repeat = futures[future]
            trace = future.result()
            save(out / f"{strategy}-{case['id']}-r{repeat}.json", trace)
            rows.append(summarize(case, trace, repeat))
            save(out / 'summary.json', summary(rows, manifest))
            print(strategy, case['id'], f'r{repeat}', trace['status'],
                  'contract_pass=' + str(trace['contract']['contract_pass']), flush=True)
    print(json.dumps(summary(rows, manifest)['strategies'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
