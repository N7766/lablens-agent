#!/usr/bin/env python3
"""Four-request maximum diagnostic experiment; separate from frozen Agent scores."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'docs/evaluations/conversation-round10'
MODEL = 'Qwen/Qwen3-235B-A22B-Instruct-2507'
BASE_URL = 'https://api.deepinfra.com/v1/openai'
CASE_IDS = ['cedar-t1', 'harbor-t1']
PHASES = ['baseline', 'repaired']


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def now(): return datetime.now(timezone.utc).isoformat()
def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
def canonical(value): return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
def git(*args): return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()

def source_map():
    paths = list((ROOT / 'lablens').glob('*.py')) + [Path(__file__).resolve(),
        ROOT / 'scripts/prepare_review_probe_round10.py']
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}

def inputs(path):
    data = read(path)
    cases = data['cases']
    if [c['turn_id'] for c in cases] != CASE_IDS:
        raise ValueError('Exactly the two predeclared case IDs are required')
    for c in cases:
        timeout = c['original_review_timeout_ms']
        if type(timeout) not in (float, int) or not 1 <= timeout <= 40000:
            raise ValueError('Diagnostic budget cannot exceed the recorded review allowance')
    return data

def verify_seal(directory):
    seal = read(directory / 'raw-seal.json')
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*')
              if p.is_file() and p.name != 'raw-seal.json'}
    if actual != set(seal['files']):
        raise ValueError('Sealed file inventory changed')
    for name, expected in seal['files'].items():
        p = (directory / name).resolve()
        if not p.is_relative_to(directory.resolve()) or sha(p) != expected:
            raise ValueError('Sealed file changed: ' + name)
    return seal

def verify_baseline_identity(plan):
    verify_seal(BASE / 'runs/baseline')
    previous = read(BASE / 'runs/baseline/plan.json')
    if previous['phase'] != 'baseline' or any(previous[key] != plan[key] for key in
            ('inputs_sha256', 'protocol_sha256', 'checklist_sha256')):
        raise ValueError('Repaired phase must use the same inputs, protocol and checklist')
    if sha(BASE / 'runs/baseline/raw-seal.json') != plan['baseline_raw_sha256']:
        raise ValueError('Baseline identity changed')

def verify(plan):
    if plan['phase'] not in PHASES or any(plan.get(key) != value for key, value in {
        'model': MODEL, 'provider_host': 'api.deepinfra.com', 'max_requests_phase': 2,
        'max_requests_experiment': 4, 'max_tokens': 2600, 'temperature': 0,
        'retry_enabled': False, 'embedding_enabled': False}.items()):
        raise ValueError('Experiment limits changed')
    if plan['source_files'] != source_map():
        raise ValueError('Candidate source inventory changed')
    if git('rev-parse', 'HEAD') != plan['candidate_sha']:
        raise ValueError('Candidate HEAD changed')
    if sha(ROOT / plan['inputs_path']) != plan['inputs_sha256']:
        raise ValueError('Frozen inputs changed')
    if sha(BASE / 'protocol.md') != plan['protocol_sha256']:
        raise ValueError('Frozen protocol changed')
    if sha(BASE / 'semantic-checklist.md') != plan['checklist_sha256']:
        raise ValueError('Frozen semantic checklist changed')
    for name, expected in plan['source_files'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('Candidate source changed: ' + name)
    if plan['phase'] == 'repaired':
        verify_baseline_identity(plan)

def freeze(phase, path):
    inputs(path)
    destination = BASE / f'{phase}-plan.json'
    if destination.exists() or (BASE / 'runs' / phase).exists():
        raise ValueError('Refuse to replace a phase plan or an attempted phase')
    head = git('rev-parse', 'HEAD'); sources = source_map()
    for name, expected in sources.items():
        value = subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT)
        if hashlib.sha256(value).hexdigest() != expected:
            raise ValueError('Source must be committed before freeze: ' + name)
    for p in (path, BASE / 'protocol.md', BASE / 'semantic-checklist.md'):
        value = subprocess.check_output(['git', 'show', head + ':' + str(p.resolve().relative_to(ROOT))], cwd=ROOT)
        if hashlib.sha256(value).hexdigest() != sha(p):
            raise ValueError('Experiment files must be committed before freeze')
    baseline = None
    if phase == 'repaired':
        verify_seal(BASE / 'runs/baseline'); baseline = sha(BASE / 'runs/baseline/raw-seal.json')
    plan = dict(phase=phase, candidate_sha=head, inputs_path=str(path.resolve().relative_to(ROOT)),
        inputs_sha256=sha(path), protocol_sha256=sha(BASE / 'protocol.md'),
        checklist_sha256=sha(BASE / 'semantic-checklist.md'), source_files=sources,
        baseline_raw_sha256=baseline, model=MODEL, provider_host='api.deepinfra.com',
        max_requests_phase=2, max_requests_experiment=4, max_tokens=2600, temperature=0,
        retry_enabled=False, embedding_enabled=False, created_utc=now())
    if phase == 'repaired': verify_baseline_identity(plan)
    dump(destination, plan); print(json.dumps({'phase':phase,'plan_sha256':sha(destination),'candidate':head}))

def run(phase, go):
    if go != 'GO': raise ValueError('Explicit GO required')
    plan = read(BASE / f'{phase}-plan.json'); verify(plan)
    if plan['phase'] != phase: raise ValueError('Phase mismatch')
    data = inputs(ROOT / plan['inputs_path'])
    from lablens import rag
    from lablens.runtime_telemetry import ExecutionTelemetry, request_body_bytes
    import httpx
    if (os.environ.get('LLM_MODEL') != MODEL or
        os.environ.get('LLM_BASE_URL', '').rstrip('/') != BASE_URL or
        not os.environ.get('LLM_API_KEY')):
        raise ValueError('Expected provider configuration is unavailable')
    # Validate every request offline before consuming the once-only phase claim.
    for case in data['cases']:
        config = rag.provider_config('generation', deadline_started=time.monotonic())
        payload = rag._generation_payload(case['question'], case['hits'], config,
            draft=case['draft'], context_data=case['context_data'])
        if phase == 'baseline' and payload != case['original_payload']:
            raise ValueError('Baseline payload differs from historical reconstruction')
        if payload['max_tokens'] != 2600 or payload['temperature'] != 0 or payload['model'] != MODEL:
            raise ValueError('Output, temperature or model budget changed')
    directory = BASE / 'runs' / phase
    directory.mkdir(parents=True, exist_ok=True)
    # A persistent claim is never removed or resumed, including after interruption.
    with (directory / 'once-only-claim.json').open('x') as stream:
        json.dump({'phase':phase,'created_utc':now(),'max_requests':2},stream)
    dump(directory / 'plan.json', plan)
    outcomes = []
    fatal = None
    original_post = rag.bounded_post
    try:
        for case in data['cases']:
            verify(plan)
            case_id = case['turn_id']; out = directory / case_id; out.mkdir()
            budget = case['original_review_timeout_ms'] / 1000
            config = rag.provider_config('generation', deadline_started=time.monotonic())
            config.update(timeout=budget, deadline=time.monotonic() + budget)
            usage = rag._new_usage(); observations = []; trace = []; sent = 0
            payload = rag._generation_payload(case['question'], case['hits'], config,
                draft=case['draft'], context_data=case['context_data'])
            if phase == 'baseline' and payload != case['original_payload']:
                raise ValueError('Baseline payload differs from historical reconstruction')
            if payload['max_tokens'] != 2600 or payload['temperature'] != 0 or payload['model'] != MODEL:
                raise ValueError('Output, temperature or model budget changed')
            dump(out / 'request.json', payload)
            started = time.monotonic()
            # Build/freeze checks do not spend the measured review allowance.
            config['deadline'] = started + budget
            telemetry = ExecutionTelemetry(trace, started=started, deadline=config['deadline'], clock=time.monotonic)
            def post(url, **kwargs):
                nonlocal sent
                if (sent or url != BASE_URL + '/chat/completions' or kwargs['json'] != payload or
                    not 0 < kwargs['timeout'] <= budget or kwargs.get('follow_redirects') is not False):
                    raise ValueError('Unexpected extra provider request or payload')
                sent += 1
                dump(out / 'request-intent.json', dict(sent_utc=now(), request_body_bytes=request_body_bytes(payload),
                    request_sha256=canonical(payload), timeout_seconds=kwargs['timeout'], max_requests=1))
                response = original_post(url, **kwargs)
                # Capture response bytes even if the production deadline rejects it later.
                (out / 'provider-response.bin').write_bytes(response.content)
                dump(out / 'response-metadata.json', dict(http_status=response.status_code,
                    body_bytes=len(response.content), body_sha256=sha(out / 'provider-response.bin')))
                return response
            rag.bounded_post = post
            outcome = 'interrupted_or_unexpected_error'
            try:
                with telemetry.bind(), telemetry.stage('review', step=1):
                    result = rag.review_assessments(case['question'], case['draft'], case['hits'], usage, config,
                        observer=lambda event: observations.append(event), context_data=case['context_data'])
                dump(out / 'result.json', result); outcome = 'review_returned'
            except rag.RAGError as exc:
                dump(out / 'failure.json', dict(status=exc.status, message=str(exc),
                    validation_detail=getattr(exc, 'review_diagnostic', None)))
                outcome = 'review_failed'
            except BaseException:
                if sent and not any(e.get('event') == 'request_finished' and e.get('usage_known')
                                    for e in trace):
                    rag._unknown_usage(usage, 'generation')
                raise
            finally:
                rag.bounded_post = original_post
                dump(out / 'observer.json', observations); dump(out / 'usage.json', usage); dump(out / 'trace.json', trace)
                dump(out / 'attempt.json', dict(case_id=case_id, sent_requests=sent,
                    elapsed_ms=round((time.monotonic()-started)*1000,3), allowance_ms=case['original_review_timeout_ms']))
                outcomes.append(dict(case_id=case_id, outcome=outcome, sent_requests=sent))
            print(json.dumps(outcomes[-1]), flush=True)
        verify(plan)
    except BaseException as exc:
        fatal = dict(type=type(exc).__name__, message='Execution stopped; inspect local diagnostic logs. No automatic resume.')
        raise
    finally:
        rag.bounded_post = original_post
        dump(directory / 'summary.json', dict(phase=phase, candidate_sha=plan['candidate_sha'],
            inputs_sha256=plan['inputs_sha256'], outcomes=outcomes, fatal=fatal,
            scope='Direct review diagnostic requests only; no Agent decision/retrieval/persistence run or answer-quality rate'))
        dump(directory / 'raw-seal.json', dict(files={str(p.relative_to(directory)):sha(p)
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'raw-seal.json'}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','run'])
    parser.add_argument('--phase',required=True,choices=PHASES)
    parser.add_argument('--inputs',type=Path,default=BASE/'inputs.json')
    parser.add_argument('--go',default='')
    args=parser.parse_args()
    if args.action=='freeze':freeze(args.phase,args.inputs)
    else:run(args.phase,args.go)

if __name__=='__main__':main()
