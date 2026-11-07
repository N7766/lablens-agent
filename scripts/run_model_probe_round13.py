#!/usr/bin/env python3
"""One durable, <=3-request transport/new-sample probe. Never retries or resumes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'docs/evaluations/conversation-round13/model'
MODEL = 'Qwen/Qwen3-235B-A22B-Instruct-2507'
BASE_URL = 'https://api.deepinfra.com/v1/openai'
MAX_REQUESTS = 3
TIMEOUTS = (10, 35, 40)
OUTPUTS = (64, 2600, 2600)
MAX_BODY_BYTES = 128 * 1024
TOTAL_SECONDS = 100


def now(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def git(*args): return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
def canonical(value): return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dump(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def exception_types(exc):
    """Retain class identity, never URLs/keys from exception text."""
    values = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(values) < 8:
        seen.add(id(exc))
        values.append(type(exc).__name__)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return values


def versions():
    return dict(python=platform.python_version(), httpx=importlib.metadata.version('httpx'),
                pydantic=importlib.metadata.version('pydantic'))


def source_map():
    paths = list((ROOT / 'lablens').glob('*.py')) + [Path(__file__).resolve()]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def tiny_payload():
    return dict(model=MODEL, temperature=0, max_tokens=64,
                messages=[dict(role='user', content='Reply with exactly OK.')])


def pricing(path):
    p = read(path)
    if (p.get('provider_base_url') != BASE_URL or p.get('model') != MODEL
            or p.get('source_url') != 'https://deepinfra.com/' + MODEL
            or p.get('checked_on') != '2026-11-08' or p.get('context_tokens') != 262144):
        raise ValueError('Pricing identity/context must match the verified protocol')
    for key in ('input_usd_per_million_upper', 'output_usd_per_million_upper', 'cost_cap_usd'):
        if type(p.get(key)) not in (int, float) or not math.isfinite(p[key]) or p[key] <= 0:
            raise ValueError('Pricing must contain finite positive rates and cap')
    if (p['input_usd_per_million_upper'] < .135 or p['output_usd_per_million_upper'] < .825
            or p['cost_cap_usd'] > 1):
        raise ValueError('Pricing must retain the conservative rate and <=1 USD cap')
    bound = (MAX_REQUESTS * p['context_tokens'] * p['input_usd_per_million_upper']
             + sum(OUTPUTS) * p['output_usd_per_million_upper']) / 1_000_000
    if bound > p['cost_cap_usd']:
        raise ValueError('Declared worst-case planned cost exceeds cap')
    return p, round(bound, 10)


def fixture():
    from lablens.rag import RAGRequest
    item = read(BASE / 'fixture.json')
    request = RAGRequest.model_validate(item['request'])
    if (item.get('fixture_id') != 'alder-confirms-20261108' or request.mode != 'live'
            or request.strategy != 'bm25' or request.top_k != 5):
        raise ValueError('Frozen fixture identity or retrieval mode changed')
    return request


def payloads():
    from lablens import rag
    request = fixture()
    result = rag.answer_rag(request.model_copy(update={'mode': 'extractive'}))
    hits = result['retrieval']['hits']
    if {h['document_id'] for h in hits} != {d.id for d in request.documents}:
        raise ValueError('Offline retrieval did not cover all fixed fixture documents')
    generation = rag._generation_payload(request.question, hits, {'model': MODEL})
    for index, payload in enumerate((tiny_payload(), generation)):
        validate_payload(payload, index)
    return dict(tiny=tiny_payload(), generation=generation), result['retrieval']


def validate_payload(payload, index):
    from lablens.runtime_telemetry import request_body_bytes
    if not 0 <= index < MAX_REQUESTS:
        raise ValueError('Unexpected additional provider request')
    if (payload.get('model') != MODEL or payload.get('max_tokens') != OUTPUTS[index]
            or payload.get('temperature') != 0 or request_body_bytes(payload) > MAX_BODY_BYTES):
        raise ValueError('Payload model/output/temperature/body ceiling changed')
    if index and payload.get('response_format', {}).get('type') != 'json_schema':
        raise ValueError('Production assessment JSON schema is required')


def verify(plan):
    fixed = dict(model=MODEL, provider_base_url=BASE_URL, max_requests=MAX_REQUESTS,
                 timeouts_seconds=list(TIMEOUTS), max_output_tokens=list(OUTPUTS),
                 max_body_bytes=MAX_BODY_BYTES, total_seconds=TOTAL_SECONDS,
                 retry_enabled=False, embedding_enabled=False)
    if any(plan.get(key) != value for key, value in fixed.items()):
        raise ValueError('Frozen limits changed')
    if plan['source_files'] != source_map() or plan['candidate_sha'] != git('rev-parse', 'HEAD'):
        raise ValueError('Candidate source inventory or HEAD changed')
    if plan['versions'] != versions():
        raise ValueError('Runtime dependency versions changed')
    for name, expected in plan['frozen_files'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('Frozen experiment file changed: ' + name)
    p, bound = pricing(ROOT / plan['pricing_path'])
    if bound != plan['planned_cost_upper_usd'] or p != plan['pricing']:
        raise ValueError('Frozen price bound changed')
    built, _ = payloads()
    if built != plan['request_payloads']:
        raise ValueError('Frozen tiny/generation payload changed')


def freeze(pricing_path):
    if (BASE / 'plan.json').exists() or (BASE / 'run').exists():
        raise ValueError('Refuse to replace an existing plan or attempted run')
    p, bound = pricing(pricing_path)
    built, retrieval = payloads()
    files = [BASE / name for name in ('fixture.json', 'semantic-checklist.md', 'protocol.md')]
    files.append(Path(pricing_path).resolve())
    sources = source_map()
    frozen = {str(path.relative_to(ROOT)): sha(path) for path in files}
    head = git('rev-parse', 'HEAD')
    for name, expected in {**sources, **frozen}.items():
        content = subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('All experiment sources must be committed before freeze: ' + name)
    plan = dict(candidate_sha=head, source_files=sources, frozen_files=frozen,
        versions=versions(), pricing=p, pricing_path=str(Path(pricing_path).resolve().relative_to(ROOT)),
        planned_cost_upper_usd=bound, request_payloads=built,
        request_payload_hashes={key: canonical(value) for key, value in built.items()},
        offline_retrieval=retrieval, created_utc=now(), model=MODEL, provider_base_url=BASE_URL,
        max_requests=MAX_REQUESTS, timeouts_seconds=list(TIMEOUTS), max_output_tokens=list(OUTPUTS),
        max_body_bytes=MAX_BODY_BYTES, total_seconds=TOTAL_SECONDS, retry_enabled=False, embedding_enabled=False)
    dump(BASE / 'plan.json', plan, exclusive=True)
    print(json.dumps(dict(plan_sha256=sha(BASE / 'plan.json'), candidate_sha=head,
                          planned_cost_upper_usd=bound)), flush=True)


def verify_seal(directory):
    files = read(directory / 'raw-seal.json')['files']
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*')
              if p.is_file() and p.name != 'raw-seal.json'}
    if actual != set(files):
        raise ValueError('Sealed run file inventory changed')
    for name, expected in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != expected:
            raise ValueError('Sealed run file changed')


def run(go):
    if go != 'GO':
        raise ValueError('Explicit GO required')
    import httpx
    from lablens import rag
    from lablens.runtime_telemetry import ExecutionTelemetry, request_body_bytes, safe_usage
    # Isolated runner only. Never load undisclosed .env after explicit preflight.
    original_dotenv = rag.load_dotenv
    rag.load_dotenv = lambda *args, **kwargs: False
    original_post = rag.bounded_post
    directory = BASE / 'run'
    original_rag_env = {name: os.environ.get(name) for name in ('RAG_TIMEOUT_SECONDS', 'RAG_TOTAL_SECONDS')}
    claimed = False
    attempts = []
    trace = []
    outcome = 'not_started'
    fatal = None
    tiny_usage = rag._new_usage()
    rag_result = None
    started = time.monotonic()
    previous_unknown = False
    try:
        plan = read(BASE / 'plan.json')
        verify(plan)
        if (os.environ.get('LLM_MODEL') != MODEL or os.environ.get('LLM_BASE_URL', '').rstrip('/') != BASE_URL
                or not os.environ.get('LLM_API_KEY')):
            raise ValueError('Expected explicit provider configuration is unavailable')
        directory.mkdir(parents=True, exist_ok=True)
        dump(directory / 'once-only-claim.json', dict(created_utc=now(), max_requests=MAX_REQUESTS,
             plan_sha256=sha(BASE / 'plan.json')), exclusive=True)
        claimed = True
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        dump(directory / 'plan.json', plan)
        dump(directory / 'transport-configuration.json', dict(trust_env=True,
            proxy_environment_present={key: bool(os.environ.get(key)) for key in
            ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')},
            route_observed=False, dotenv_loading_enabled=False))
        started = time.monotonic()
        deadline = started + TOTAL_SECONDS
        telemetry = ExecutionTelemetry(trace, started=started, deadline=deadline, clock=time.monotonic)
        def post(url, **kwargs):
            nonlocal previous_unknown
            index = len(attempts)
            remaining = deadline - time.monotonic()
            if previous_unknown:
                raise rag.RAGError('Probe stopped: previous provider usage is unknown; no further request was sent')
            if index >= MAX_REQUESTS or remaining <= 0:
                raise rag.RAGError('Probe request count or total start budget exhausted; no further request was sent')
            verify(plan)
            payload = kwargs.get('json')
            validate_payload(payload, index)
            if (url != BASE_URL + '/chat/completions' or kwargs.get('follow_redirects') is not False
                    or type(kwargs.get('timeout')) not in (int, float) or not 0 < kwargs['timeout'] <= 180):
                raise ValueError('Unexpected provider transport options')
            if index < 2 and payload != plan['request_payloads'][('tiny', 'generation')[index]]:
                raise ValueError('Actual request differs from the frozen request')
            if index == 2:
                user = json.loads(payload['messages'][1]['content'])
                if ('untrusted_draft' not in user or user['question'] != fixture().question
                        or user['evidence'] != json.loads(plan['request_payloads']['generation']['messages'][1]['content'])['evidence']):
                    raise ValueError('Review must use the frozen question and retrieved evidence')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise rag.RAGError('Probe total start budget exhausted during preflight; no further request was sent')
            timeout = min(kwargs['timeout'], TIMEOUTS[index], remaining)
            kwargs['timeout'] = timeout
            phase = ('tiny', 'generation', 'review')[index]
            out = directory / f'{index + 1:02d}-{phase}'
            out.mkdir()
            dump(out / 'request.json', payload)
            intent = dict(phase=phase, attempt=index + 1, started_utc=now(),
                          body_bytes=request_body_bytes(payload), request_sha256=canonical(payload),
                          wire_body_sha256=hashlib.sha256(httpx.Request('POST', url, json=payload).content).hexdigest(),
                          timeout_seconds=timeout, max_output_tokens=OUTPUTS[index],
                          provider_acceptance='unknown', usage_known=False,
                          usage=safe_usage(None), status='attempt_started')
            dump(out / 'request-intent.json', intent)
            attempts.append(intent)
            previous_unknown = True
            call_started = time.monotonic()
            try:
                response = original_post(url, **kwargs)
                (out / 'provider-response.bin').write_bytes(response.content)
                intent.update(status='response_received', http_status=response.status_code,
                              provider_acceptance='response_received')
                dump(out / 'response-metadata.json', dict(http_status=response.status_code,
                    body_bytes=len(response.content), body_sha256=sha(out / 'provider-response.bin')))
                try: body = response.json()
                except (ValueError, TypeError): body = None
                usage = safe_usage(body.get('usage') if isinstance(body, dict) else None)
                intent.update(usage=usage, usage_known=response.is_success and all(v is not None for v in usage.values()))
                previous_unknown = not intent['usage_known'] or not response.is_success
                return response
            except BaseException as exc:
                intent.update(status='exception', exception_types=exception_types(exc))
                try: dump(out / 'transport-failure.json', dict(exception_types=exception_types(exc)))
                except Exception: pass  # Preserve the original transport/interruption exception.
                raise
            finally:
                intent['elapsed_ms'] = round((time.monotonic() - call_started) * 1000, 3)
                active_error = sys.exc_info()[0] is not None
                try: dump(out / 'attempt.json', intent)
                except Exception:
                    if not active_error: raise
        rag.bounded_post = post
        os.environ['RAG_TIMEOUT_SECONDS'] = '40'
        os.environ['RAG_TOTAL_SECONDS'] = '90'
        outcome = 'tiny_failed'
        try:
            config = rag.provider_config('generation', deadline_started=started)
            config.update(timeout=10, deadline=min(deadline, time.monotonic() + 10))
            with telemetry.bind(), telemetry.stage('tiny', step=1):
                body = rag._post(config, '/chat/completions', tiny_payload(), tiny_usage, 'generation')
            choice = body.get('choices', [{}])[0]
            if (previous_unknown or choice.get('finish_reason') != 'stop'
                    or choice.get('message', {}).get('content', '').strip() != 'OK'):
                raise rag.RAGError('Tiny probe was incomplete, unexpected or had unknown usage; dependent RAG was not run')
            outcome = 'rag_started'
            with telemetry.bind(), telemetry.stage('rag_pipeline', step=2):
                rag_result = rag.answer_rag(fixture(), budget_seconds=min(90, max(.001, deadline - time.monotonic())))
            dump(directory / 'result.json', rag_result)
            outcome = 'report_returned' if rag_result.get('assessments') else 'rag_failed'
        except rag.RAGError as exc:
            dump(directory / 'failure.json', dict(status=exc.status, message=str(exc),
                exception_types=exception_types(exc)))
        verify(plan)
    except BaseException as exc:
        fatal = dict(exception_types=exception_types(exc), message='Stopped; no automatic retry or resume.')
        raise
    finally:
        rag.bounded_post = original_post
        rag.load_dotenv = original_dotenv
        for key, value in original_rag_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        if claimed:
            active_error = sys.exc_info()[0] is not None
            try:
                if any(not attempt['usage_known'] for attempt in attempts):
                    total_usage = safe_usage(None)
                else:
                    total_usage = {key: sum(a['usage'][key] for a in attempts) for key in safe_usage(None)}
                if attempts and not attempts[0]['usage_known']:
                    rag._unknown_usage(tiny_usage, 'generation')
                dump(directory / 'trace.json', trace)
                dump(directory / 'usage.json', dict(tiny=tiny_usage, rag=rag_result.get('usage') if rag_result else None,
                     all_attempts_usage_known=all(a['usage_known'] for a in attempts), aggregate_tokens=total_usage))
                dump(directory / 'summary.json', dict(outcome=outcome, attempts=attempts,
                    sent_requests=len(attempts), fatal=fatal, elapsed_ms=round((time.monotonic()-started)*1000, 3),
                    dependent_rag='not_run' if len(attempts) < 2 else 'executed',
                    final_semantic_status='not_scored' if outcome == 'report_returned' else 'not_evaluable',
                    scope='One tiny request then direct BM25 RAG generation/review; no Agent tools or persistence.'))
                dump(directory / 'raw-seal.json', dict(files={str(p.relative_to(directory)): sha(p)
                     for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'raw-seal.json'}))
            except Exception:
                if not active_error: raise
    print(json.dumps(dict(outcome=outcome, sent_requests=len(attempts))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'run', 'verify-seal'])
    parser.add_argument('--pricing', type=Path, default=BASE / 'pricing.json')
    parser.add_argument('--go', default='')
    args = parser.parse_args()
    if args.action == 'freeze': freeze(args.pricing)
    elif args.action == 'run': run(args.go)
    else: verify_seal(BASE / 'run')


if __name__ == '__main__':
    main()
