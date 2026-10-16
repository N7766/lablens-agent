"""Frozen mixed-task contracts, with separate natural and injected-repair experiments."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

from lablens.datasets import DatasetBundle
from lablens.guided_planning import GUIDED_SYSTEM_V2, ProposalV2, draft_guided
from lablens.model import HTTPModel
from lablens.planning import PlanRequest, execute_plan, preview_plan

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('task_contracts', ROOT / 'scripts/evaluate_snapshots.py')
_contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contracts)
save = _contracts.save
snapshot_digest = _contracts.snapshot_digest
oracle_plan = _contracts.oracle_plan
receipt_check = _contracts.receipt_check
ARMS = ('task_rules_off', 'task_rules_on')
WARNING = ('Small predeclared engineering contracts on an existing DPO validation-summary snapshot and the '
           'existing synthetic Atlas fixture, not new model experiment measurements, independent human review '
           'or general semantic accuracy. Natural arms use independent draws with identical v2 prompt/schema, '
           'model, query rules, data and two-request budget; only task-rule checking/feedback is toggled. '
           'First-attempt scoring replays the first already-returned message locally, without additional sampling. '
           'Controlled repairs seed an authored faulty first proposal, then allow at most one live replacement; '
           'they are not natural model failures and are never pooled with natural success rates. All failures '
           'are retained. Receipt consistency is separate from contract success. Concurrency affects latency.')


def load_cases():
    return json.loads((ROOT / 'evaluation/v0.10/cases.json').read_text())


def load_controls():
    return json.loads((ROOT / 'evaluation/v0.10/controls.json').read_text())


def load_bundle(case):
    return json.loads((ROOT / case['snapshot_file']).read_text())


def grade_contract(case, bundle, trace):
    result = _contracts.grade_contract(case, bundle, trace)
    result['scope'] = 'Predeclared mixed-task engineering contract on existing snapshots; not general semantic or independent human review'
    return result


def execute_prepared(case, bundle, trace):
    if trace['status'] == 'needs_review':
        request = PlanRequest(question=case['question'], plan=trace['plan'], dataset=bundle,
                              preview_id=trace['preview']['preview_id'])
        trace['execution'] = execute_plan(request)
        trace['status'] = trace['execution']['status']
    return trace


class FirstReply:
    mode = 'recorded_first_reply'

    def __init__(self, message):
        self.message = deepcopy(message)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError('First-attempt scoring must not request another reply')
        return deepcopy(self.message), dict(prompt_tokens=0, completion_tokens=0, estimated_cost=0,
                                           source='replayed_not_billed')


def first_attempt_contract(case, bundle, trace, arm):
    events = [e for e in trace.get('events', []) if e.get('type') == 'model']
    if not events:
        return dict(status='no_model_reply', contract_pass=False, failures=['No returned first message'],
                    additional_provider_requests=0)
    try:
        replay = draft_guided(FirstReply(events[0]['message']), case['question'], max_attempts=1,
                              check_request_rules=True, check_task_rules=arm == 'task_rules_on',
                              dataset=DatasetBundle.model_validate(bundle), proposal_version='v2')
        execute_prepared(case, bundle, replay)
        return dict(status=replay['status'], **grade_contract(case, bundle, replay),
                    additional_provider_requests=0, method='Local replay of first message; no new sampling')
    except Exception as exc:
        return dict(status='scoring_error', contract_pass=False, failures=[f'{type(exc).__name__}: {str(exc)[:1000]}'],
                    additional_provider_requests=0)


def _finish(case, bundle, trace, repeat, arm, started, *, live, suite):
    trace.update(case_id=case['id'], repeat=repeat, arm=arm, suite=suite,
                 review_mode='unreviewed_evaluation' if live else 'predeclared_oracle',
                 latency_seconds=round(time.monotonic() - started, 3))
    try:
        trace['contract'] = grade_contract(case, bundle, trace)
    except (ValueError, TypeError, KeyError) as exc:
        trace['contract'] = dict(contract_pass=False, failures=[f'Invalid trace: {type(exc).__name__}'])
    trace['receipt_check'] = receipt_check(trace)
    trace['first_attempt_contract'] = first_attempt_contract(case, bundle, trace, arm) if live else dict(
        status='not_applicable_offline_oracle', contract_pass=None, additional_provider_requests=0)
    return trace


def execute_case(case, repeat=1, *, arm='task_rules_on', live=False, bundle=None):
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    bundle = deepcopy(bundle) if bundle is not None else load_bundle(case)
    started = time.monotonic()
    trace = dict(question=case['question'], snapshot_id=case['expected_snapshot_id'], input_dataset=bundle,
                 status='harness_error', events=[], model_calls=0, usage_incomplete=False)
    try:
        if live:
            trace = draft_guided(HTTPModel(), case['question'], max_attempts=2,
                                 check_request_rules=True, check_task_rules=arm == 'task_rules_on',
                                 dataset=DatasetBundle.model_validate(bundle), proposal_version='v2')
        else:
            reference = PlanRequest(question=case['question'], plan=oracle_plan(case), dataset=bundle)
            trace.update(mode='offline_oracle', plan=reference.plan.model_dump(), status='needs_review',
                         preview=preview_plan(reference))
        execute_prepared(case, bundle, trace)
    except Exception as exc:
        trace.update(status='harness_error', error=f'{type(exc).__name__}: {str(exc)[:1000]}')
    trace['live_provider_calls'] = trace['model_calls']
    return _finish(case, bundle, trace, repeat, arm, started, live=live, suite='natural')


def proposal_message(case):
    """Positive control from literal expectations; never given to the natural model."""
    actions = []
    for i, item in enumerate(case['expected_steps'], 1):
        action = dict(id=f's{i}', clause_id=f'c{i}', operation=item['kind'])
        if item['kind'] == 'query':
            q = deepcopy(item['parameters'])
            descending = q.pop('descending', True)
            boundary = q.get('top_k')
            q.update(order_by=q.get('order_by', 'accuracy'), direction='descending' if descending else 'ascending',
                     selection='all' if boundary is None else 'top_k', top_k=boundary,
                     include_ties=q.get('include_ties', True))
            action['query'] = q
        elif item['kind'] == 'compare':
            action.update(baseline=item['baseline_id'], candidate=item['candidate_id'])
        actions.append(action)
    return dict(role='assistant', content=None, tool_calls=[dict(id='seed-proposal', type='function', function=dict(
        name='propose_analysis', arguments=json.dumps(dict(actions=actions), ensure_ascii=False)))])


def injected_message(case, control):
    message = proposal_message(case)
    proposal = json.loads(message['tool_calls'][0]['function']['arguments'])
    actions = proposal['actions']
    pair = next(a for a in actions if a['operation'] == 'compare')
    if control['mutation'] == 'reverse_comparison':
        pair['baseline'], pair['candidate'] = pair['candidate'], pair['baseline']
    elif control['mutation'] == 'replace_description_with_comparison':
        description = next(a for a in actions if a['operation'] == 'describe')
        description.update(operation='compare', baseline=pair['baseline'], candidate=pair['candidate'])
    elif control['mutation'] == 'duplicate_comparison':
        duplicate = deepcopy(pair)
        duplicate['id'] = 'duplicate'
        actions.append(duplicate)
    else:
        raise ValueError('Unknown control mutation')
    message['tool_calls'][0]['function']['arguments'] = json.dumps(proposal, ensure_ascii=False)
    return message


class SeededRepair:
    """An authored first proposal plus at most one separately counted provider request."""
    mode = 'injected_first_proposal'

    def __init__(self, first, replacement=None):
        self.first = deepcopy(first)
        self.replacement = replacement
        self.calls = 0
        self.provider_calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return deepcopy(self.first), dict(prompt_tokens=0, completion_tokens=0, estimated_cost=0,
                                              source='authored_fault_not_billed')
        if self.calls != 2:
            raise AssertionError('Injected repair exceeds one replacement')
        if self.replacement is not None:
            return deepcopy(self.replacement), dict(prompt_tokens=0, completion_tokens=0, estimated_cost=0,
                                                    source='authored_positive_control_not_billed')
        model = HTTPModel()
        self.provider_calls += 1
        return model.complete(messages, tools)


def execute_control(case, control, *, live=False, bundle=None, arm='task_rules_on', first_message=None):
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    bundle = deepcopy(bundle) if bundle is not None else load_bundle(case)
    started = time.monotonic()
    model = SeededRepair(first_message if first_message is not None else injected_message(case, control),
                         None if live else proposal_message(case))
    trace = dict(question=case['question'], snapshot_id=case['expected_snapshot_id'], input_dataset=bundle,
                 status='harness_error', events=[], model_calls=0, usage_incomplete=False)
    try:
        trace = draft_guided(model, case['question'], max_attempts=2, check_request_rules=True,
                             check_task_rules=arm == 'task_rules_on', dataset=DatasetBundle.model_validate(bundle),
                             proposal_version='v2')
        execute_prepared(case, bundle, trace)
    except Exception as exc:
        trace.update(status='harness_error', error=f'{type(exc).__name__}: {str(exc)[:1000]}')
    _finish(case, bundle, trace, 1, arm, started, live=True, suite='controls')
    trace.update(control_id=control['id'], injected_first_proposal=True,
                 live_provider_calls=model.provider_calls, planner_completion_calls=trace['model_calls'],
                 natural_model_draw=False, replacement_mode='live' if live else 'authored_positive_control',
                 first_proposal_origin='Predeclared fault injection, not a sampled model failure')
    if not live:
        trace['review_mode'] = 'authored_positive_control'
    return trace


def freeze_sources(out, *, live, repeats, workers, suite='natural'):
    if suite not in {'natural', 'controls'}:
        raise ValueError('Unknown suite')
    files = sorted(set(list((ROOT / 'lablens').glob('*.py'))
                       + list((ROOT / 'lablens/data').glob('*.json'))
                       + list((ROOT / 'evaluation/v0.10').rglob('*'))
                       + [ROOT / 'evaluation/v0.9/fixtures/source-atlas.json',
                          ROOT / 'scripts/evaluate_snapshots.py', Path(__file__).resolve()]))
    hashes = {}
    for source in files:
        if not source.is_file():
            continue
        relative = source.relative_to(ROOT)
        target = out / 'frozen-source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    (out / 'v2-system-prompt.txt').write_text(GUIDED_SYSTEM_V2)
    save(out / 'v2-proposal-schema.json', ProposalV2.model_json_schema())
    protocol = dict(prompt_file='v2-system-prompt.txt', schema_file='v2-proposal-schema.json')
    for kind in ('prompt', 'schema'):
        protocol[kind + '_sha256'] = hashlib.sha256((out / protocol[kind + '_file']).read_bytes()).hexdigest()
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), mode='live' if live else 'offline_oracle',
                    suite=suite, git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    source_sha256=hashes, protocol=protocol,
                    cases=json.loads((out / 'frozen-source/evaluation/v0.10/cases.json').read_text()),
                    controls=json.loads((out / 'frozen-source/evaluation/v0.10/controls.json').read_text()),
                    arms=list(ARMS) if suite == 'natural' else ['task_rules_on'],
                    repeats=repeats if suite == 'natural' else 1, workers=workers,
                    max_provider_requests_per_draw=(2 if suite == 'natural' else 1) if live else 0,
                    max_planner_completions_per_draw=2, request_rules_enabled=True, proposal_version='v2',
                    warning=WARNING, raw_provider_response_scope='HTTPModel message and usage only; no response envelope or headers')
    # Freeze the actual injected tool messages, not only mutation instructions.
    by_id = {case['id']: case for case in manifest['cases']}
    manifest['control_proposals'] = {}
    for control in manifest['controls']:
        case = by_id[control['case_id']]
        declared = {}
        for kind, message in [('first', injected_message(case, control)), ('positive', proposal_message(case))]:
            name = f"control-{control['id']}-{kind}-proposal.json"
            save(out / name, message)
            declared[kind + '_file'] = name
            declared[kind + '_sha256'] = hashlib.sha256((out / name).read_bytes()).hexdigest()
        manifest['control_proposals'][control['id']] = declared
    save(out / 'manifest.json', manifest)
    return manifest


def summarize(case, trace, repeat):
    row = _contracts.summarize(case, trace, repeat)
    row.update(arm=trace['arm'], suite=trace['suite'], live_provider_calls=trace['live_provider_calls'],
               first_contract_pass=trace['first_attempt_contract']['contract_pass'],
               task_conflicts=sum(e.get('type') == 'task_check' and e.get('report', {}).get('has_conflicts', False)
                                  for e in trace.get('events', [])),
               control_id=trace.get('control_id'), injected_first_proposal=bool(trace.get('injected_first_proposal')))
    if trace['suite'] == 'controls':
        row['planner_completion_calls'] = row.pop('model_calls')
        row['replacement_attempts'] = row.pop('repair_calls')
    return row


def aggregate(rows, *, suite='natural'):
    if suite == 'controls':
        return dict(controls=len(rows), final_contract_pass=sum(r['contract_pass'] for r in rows),
                    receipts_verified=sum(r['receipt_verified'] for r in rows),
                    live_provider_calls=sum(r['live_provider_calls'] for r in rows),
                    planner_completion_calls=sum(r['planner_completion_calls'] for r in rows),
                    known_reported_estimated_cost_usd=sum(r['reported_estimated_cost_usd'] or 0 for r in rows),
                    unknown_usage_controls=[r['control_id'] for r in rows if r['usage_incomplete']],
                    natural_model_draws=0, warning='Authored faults; not a natural failure recovery rate')
    result = _contracts.aggregate(rows)
    result.update(first_contract_pass=sum(r['first_contract_pass'] is True for r in rows),
                  first_contract_assessed=sum(r['first_contract_pass'] is not None for r in rows),
                  repair_runs=sum(r['repair_calls'] > 0 for r in rows),
                  repaired_contract_success=sum(r['repair_calls'] > 0 and r['contract_pass'] for r in rows),
                  task_conflicts=sum(r['task_conflicts'] for r in rows),
                  live_provider_calls=sum(r['live_provider_calls'] for r in rows))
    result['unknown_usage_draws'] = [f"{r['arm']}-{r['case_id']}-r{r['repeat']}" for r in rows if r['usage_incomplete']]
    return result


def summary(rows, manifest):
    suite = manifest['suite']
    return dict(mode=manifest['mode'], suite=suite,
                cases=sorted(rows, key=lambda r: (r['arm'], r['case_id'], r['repeat'])),
                arms={arm: aggregate([r for r in rows if r['arm'] == arm], suite=suite) for arm in manifest['arms']},
                aggregate=aggregate(rows, suite=suite), warning=WARNING)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Enable bounded provider requests after freezing')
    parser.add_argument('--suite', choices=('natural', 'controls'), default='natural')
    parser.add_argument('--repeats', type=int, choices=(1, 2), default=2)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports')
    args = parser.parse_args()
    out = args.output / datetime.now(timezone.utc).strftime('tasks-%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    manifest = freeze_sources(out, live=args.live, repeats=args.repeats, workers=args.workers, suite=args.suite)
    if args.live:
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
        futures = {}
        if args.suite == 'natural':
            for case in manifest['cases']:
                bundle = json.loads((out / 'frozen-source' / case['snapshot_file']).read_text())
                for repeat in range(1, args.repeats + 1):
                    for arm in ARMS:
                        futures[pool.submit(execute_case, case, repeat, arm=arm, live=args.live, bundle=bundle)] = (arm, case, repeat, None)
        else:
            by_id = {case['id']: case for case in manifest['cases']}
            for control in manifest['controls']:
                case = by_id[control['case_id']]
                bundle = json.loads((out / 'frozen-source' / case['snapshot_file']).read_text())
                first_file = manifest['control_proposals'][control['id']]['first_file']
                first_message = json.loads((out / first_file).read_text())
                futures[pool.submit(execute_control, case, control, live=args.live, bundle=bundle,
                                    first_message=first_message)] = ('task_rules_on', case, 1, control['id'])
        for future in as_completed(futures):
            arm, case, repeat, control_id = futures[future]
            trace = future.result()
            save(out / (f"control-{control_id}.json" if control_id else f"{arm}-{case['id']}-r{repeat}.json"), trace)
            rows.append(summarize(case, trace, repeat))
            save(out / 'summary.json', summary(rows, manifest))
            print(arm, control_id or case['id'], f'r{repeat}', trace['status'],
                  'contract_pass=' + str(trace['contract']['contract_pass']), flush=True)
    print(json.dumps(summary(rows, manifest)['arms'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
