#!/usr/bin/env python3
"""Prepare one synthetic workspace, then submit exactly one isolated Agent turn.

prepare performs local auth/document/index operations, never generation. run claims
its output directory before submitting once; unknown submissions are not retried.
fixture is a deterministic loopback HTTP server, NOT an LLM or semantic evaluator.
Use an independently bounded gateway before a paid model; this driver does not rent
resources or grant permission to invoke a provider.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import httpx

from lablens.rag import _strict_generation_json
from lablens.review_decisions import VERSION as REVIEW_PROTOCOL

VERSION = 'private-agent-acceptance-v1'
QUESTION = '资料是否包含 SFT 基线？配置能否证明历史运行启用了缓存？'
DOCUMENTS = [
    dict(id='baseline', title='Synthetic SFT result', source='synthetic/results.md',
         text='本表仅为部署兼容性测试数据。\nSFT 基线：准确率 86%。'),
    dict(id='config', title='Synthetic cache configuration', source='synthetic/config.yaml',
         text='cache_enabled: true\n# 没有提供对应历史运行日志。'),
]
SCOPE = 'Single synthetic Agent chain; not independent semantic evaluation, concurrency or production traffic.'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write(path, value):
    with Path(path).open('xb') as out:
        out.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b'\n')
        out.flush()
        os.fsync(out.fileno())


def sources():
    return {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in
            [*sorted((ROOT / 'lablens').rglob('*.py')), Path(__file__).resolve()]}


def isolated_url(base):
    parsed = urlsplit(base)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port != 8086
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('This acceptance driver requires isolated http://127.0.0.1:8086.')
    return base


@contextmanager
def session(base):
    with httpx.Client(base_url=isolated_url(base), trust_env=False,
                      headers={'Origin': base}, timeout=15) as client:
        r = client.post('/api/auth/login', json={
            'username': os.environ.get('LABLENS_AUTH_BOOTSTRAP_USERNAME', 'admin'),
            'password': os.environ['LABLENS_AUTH_BOOTSTRAP_PASSWORD']})
        r.raise_for_status()
        client.headers['X-CSRF-Token'] = r.json()['csrf_token']
        yield client


def prepare(output, base, case, gateway_plan_sha256=None):
    if gateway_plan_sha256 is not None and not re.fullmatch('[a-f0-9]{64}', gateway_plan_sha256):
        raise ValueError('A frozen gateway plan SHA256 is required, not a mutable filename.')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'preparation-intent.json', dict(case=case, scope=SCOPE, generation_requests=0))
    with session(base) as c:
        r = c.post('/api/workspaces', json={'name': 'Round19b synthetic ' + case})
        r.raise_for_status()
        wid = r.json()['id']
        path = '/api/workspaces/' + wid
        docs = DOCUMENTS if case != 'insufficient' else [dict(
            id='unrelated', title='Synthetic unrelated record', text='The notebook cover is blue.', source='synthetic/unrelated.md')]
        for doc in docs:
            r = c.put(path + '/documents/' + doc['id'], json={k: v for k, v in doc.items() if k != 'id'})
            r.raise_for_status()
        r = c.get(path + '/documents'); r.raise_for_status()
        snapshot = r.json()
        write(output / 'ordered-snapshot.json', snapshot)
        r = c.post(path + '/index', json={'strategy': 'bm25', 'expected_revision': snapshot['revision']})
        r.raise_for_status()
        index = r.json()
        write(output / 'index.json', index)
        if index.get('status') != 'ready' or not index.get('current'):
            raise ValueError('Candidate BM25 index is not ready for this revision.')
        r = c.post(path + '/conversations', json={'title': 'Single bounded synthetic Agent acceptance'})
        r.raise_for_status()
        conv = r.json()
    plan = dict(version=VERSION, base_url=base, workspace_id=wid, conversation_id=conv['id'],
                case=case, question=QUESTION if case != 'insufficient' else '未提供的本次 GPU 总费用是多少？',
                revision=snapshot['revision'], snapshot_sha256=sha(canonical(snapshot)),
                index_id=index['index_id'], documents_sha256=index['documents_sha256'],
                source_files=sources(), review_protocol=REVIEW_PROTOCOL,
                gateway_plan_sha256=gateway_plan_sha256,
                evidence_kind='real_private_model' if gateway_plan_sha256 else 'local_scripted',
                planned_max_model_requests=3, submission_attempts=1, retries=0,
                idempotency_key=str(uuid.uuid4()), scope=SCOPE)
    write(output / 'plan.json', plan)
    with (output / 'plan.sha256').open('x') as f:
        f.write(sha((output / 'plan.json').read_bytes()) + '\n')
    return dict(status='prepared_no_generation', workspace_id=wid, case=case)


def verify_plan(output):
    raw = (output / 'plan.json').read_bytes()
    plan = _strict_generation_json(raw)
    if (sha(raw) != (output / 'plan.sha256').read_text().strip()
            or plan['source_files'] != sources() or plan['version'] != VERSION
            or plan['review_protocol'] != REVIEW_PROTOCOL):
        raise ValueError('Candidate plan/source drift; prepare a new candidate, never retry the old run.')
    isolated_url(plan['base_url'])
    return plan, raw, sha(raw)


def gateway_observation(plan, *, require_active=True):
    if not plan.get('gateway_plan_sha256'):
        raise ValueError('Real-model run requires a gateway plan bound during preparation.')
    with httpx.Client(trust_env=False, timeout=3) as c:
        r = c.get('http://127.0.0.1:18081/health', headers={
            'Authorization': 'Bearer ' + os.environ['LABLENS_PRIVATE_GATEWAY_KEY']})
        r.raise_for_status()
        status = r.json()
    if (status.get('plan_sha256') != plan['gateway_plan_sha256']
            or status.get('phase') != 'agent' or status.get('upstream_mode') != 'ssh_tunnel'
            or (require_active and status.get('blocked'))):
        raise ValueError('Live gateway does not match the frozen Agent allocation.')
    return status


def run(output, evidence_kind):
    plan, frozen_raw, frozen_digest = verify_plan(output)
    if evidence_kind != plan['evidence_kind']:
        raise ValueError('Evidence kind cannot override the frozen plan.')
    dest = output / 'run'
    dest.mkdir(exist_ok=False)
    with (dest / 'frozen-plan.json').open('xb') as out:
        out.write(frozen_raw); out.flush(); os.fsync(out.fileno())
    write(dest / 'intent.json', dict(plan_sha256=frozen_digest,
                                    evidence_kind=evidence_kind, submission_attempts=1, scope=SCOPE))
    if evidence_kind == 'real_private_model':
        if plan['case'] != 'report':
            raise ValueError('The bounded real-model plan permits one report case only.')
        before = gateway_observation(plan)
        write(dest / 'gateway-before.json', before)
        if before.get('reserved') != 0 or before.get('completed') != 0:
            raise ValueError('Agent allocation was already used; no turn submitted.')
    path = '/api/workspaces/' + plan['workspace_id']
    with session(plan['base_url']) as c:
        r = c.get(path + '/documents'); r.raise_for_status()
        if sha(canonical(r.json())) != plan['snapshot_sha256']:
            raise ValueError('Workspace changed after freeze; no Agent turn was submitted.')
        payload = dict(question=plan['question'], strategy='bm25', top_k=3, mode='agent',
                       approved_revision=plan['revision'], expected_conversation_revision=0)
        write(dest / 'submit-intent.json', dict(payload=payload, idempotency_key=plan['idempotency_key']))
        r = c.post(path + '/conversations/' + plan['conversation_id'] + '/turns',
                   json=payload, headers={'Idempotency-Key': plan['idempotency_key']})
        # Unknown outcomes must be reconciled by GET/history, never repeated here.
        write(dest / 'admission.json', dict(status_code=r.status_code, body=r.json()))
        r.raise_for_status()
        tid = r.json()['id']
        deadline = time.monotonic() + 105
        while True:
            r = c.get(path + '/tasks/' + tid); r.raise_for_status()
            task = r.json()
            if task['status'] not in ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED'):
                break
            if time.monotonic() >= deadline:
                write(dest / 'last-observed-task.json', task)
                raise TimeoutError('Task observation deadline; do not resubmit.')
            time.sleep(.2)
        write(dest / 'task-final.json', task)
    # Brand new authenticated session: report is not a Python/UI in-memory object.
    with session(plan['base_url']) as c:
        r = c.get(path + '/tasks/' + tid); r.raise_for_status()
        refreshed = r.json()
        r = c.get(path + '/conversations/' + plan['conversation_id']); r.raise_for_status()
        conversation = r.json()
        write(dest / 'task-refreshed.json', refreshed)
        write(dest / 'conversation-refreshed.json', conversation)
    result = task.get('result') or task.get('error_result') or {}
    trace = result.get('agent', {}).get('trace', [])
    expected = {'report': ('SUCCEEDED', 'completed'), 'insufficient': ('SUCCEEDED', 'insufficient_evidence'),
                'timeout': ('FAILED', 'timeout')}[plan['case']]
    checks = {
        'terminal_state': (task['status'], result.get('agent', {}).get('state')) == expected,
        'history_same_result': all(task.get(k) == refreshed.get(k) for k in ('result', 'error_result', 'status', 'error_code')),
        'conversation_links_task': conversation.get('revision') == 1 and any(
            t.get('task_id') == tid for t in conversation.get('turns', [])),
        'no_cache_substitute': not task.get('cache_hit', False),
    }
    turn = next((t for t in conversation.get('turns', []) if t.get('task_id') == tid), {})
    published = task.get('result') or {}
    checks['conversation_preserves_terminal'] = (turn.get('status') == task['status']
        and turn.get('error_code') == task.get('error_code')
        and turn.get('answer') == published.get('answer')
        and turn.get('result_status') == published.get('status')
        and turn.get('snapshot_id') == plan['workspace_id'] + ':' + str(plan['revision']))
    if plan['case'] != 'timeout':
        searches = result.get('retrieval', {}).get('searches', [])
        snapshot = json.loads((output / 'ordered-snapshot.json').read_text())
        documents = {d['id']: d for d in snapshot['documents']}
        evidence = [*result.get('retrieval', {}).get('hits', []), *result.get('citations', [])]
        hits = result.get('retrieval', {}).get('hits', [])
        validations = [e['output'] for e in trace if e['event'] == 'source_validation']
        def source_matches(hit):
            doc = documents.get(hit.get('document_id'))
            return bool(doc and hit.get('text') == doc['text'][hit['start']:hit['end']]
                        and hit.get('content_sha256') == sha(hit['text'].encode()))
        checks.update(
            real_tool_chain=[e['name'] for e in trace if e['event'] == 'tool'] == ['search_workspace', 'verify_and_submit_report'],
            source_validation_present=any(e['event'] == 'source_validation' for e in trace),
            verified_sources_bound=bool(validations) and all(
                set(v.get('chunk_ids', [])) == {h['chunk_id'] for h in hits}
                and v.get('snapshot_id') == plan['workspace_id'] + ':' + str(plan['revision'])
                and v.get('documents_sha256') == plan['documents_sha256'] for v in validations),
            review_completed=any(e['event'] == 'review_completed' for e in trace),
            candidate_review=result.get('generation_review', {}).get('payload_format') == REVIEW_PROTOCOL,
            three_model_http_calls=result.get('usage', {}).get('model_calls') == 3,
            snapshot_bound=result.get('agent', {}).get('snapshot_id') == plan['workspace_id'] + ':' + str(plan['revision']),
            actual_index_reused=bool(searches) and all(s.get('index_id') == plan['index_id']
                and s.get('index_reused') is True and s.get('transport') == 'loopback_http'
                and s.get('documents_sha256') == plan['documents_sha256']
                and s.get('index_fallback_reason') is None for s in searches),
            source_text_offsets_hashes_match=all(source_matches(hit) for hit in evidence))
    if plan['case'] == 'report':
        assessments = result.get('assessments', [])
        checks.update(
            baseline_supported=any('SFT' in a.get('statement', '') and a.get('verdict') == 'supported'
                                   and a.get('quotes') for a in assessments),
            historical_cache_insufficient=any('缓存' in a.get('statement', '') and a.get('verdict') == 'insufficient'
                                              for a in assessments))
    if plan['case'] == 'insufficient':
        checks.update(refused=result.get('status') == 'refused', no_citations=not result.get('citations'),
                      verdict_insufficient=result.get('evidence_verdict') == 'insufficient')
    elif plan['case'] == 'timeout':
        checks.update(timeout_code=task.get('error_code') == 'agent_timeout',
                      report_withheld=task.get('result') is None,
                      one_attempt=result.get('usage', {}).get('model_calls') == 1,
                      usage_unknown=result.get('usage', {}).get('provider_usage_unknown') is True)
        # Local fixture has a three-second late response; it cannot publish later.
        time.sleep(4)
        with session(plan['base_url']) as c:
            r = c.get(path + '/tasks/' + tid); r.raise_for_status()
            late = r.json(); write(dest / 'late-task.json', late)
        checks['late_response_cannot_change_terminal'] = all(
            late.get(k) == task.get(k) for k in ('result', 'error_result', 'status', 'error_code'))
    if evidence_kind == 'real_private_model':
        after = gateway_observation(plan, require_active=False)
        write(dest / 'gateway-after.json', after)
        checks['gateway_three_completed'] = after.get('reserved') == after.get('completed') == 3 and not after.get('blocked')
    summary = dict(passed=all(checks.values()), checks=checks, task_id=tid, evidence_kind=evidence_kind,
                   semantic_acceptance='not_evaluated', scope=SCOPE)
    write(dest / 'summary.json', summary)
    return summary


def fixture_content(payload, case):
    """Scripted decisions exercise real transport; they do not evaluate a model."""
    users = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'user']
    name = payload['response_format']['json_schema']['name']
    if name == 'evidence_review_by_id':
        data = users[-1]
        decisions = {}
        for row in data['untrusted_draft']['assessments']:
            identity = row['assessment_id']
            if identity in data['server_review_decisions']['retainable_assessment_ids']:
                decisions[identity] = {'action': 'retain'}
            else:
                decisions[identity] = dict(action='replace', verdict='insufficient',
                    explanation='缺少对应历史运行记录，不能确定。', basis='historical_observation', evidence_ids=[])
        return {'decisions': decisions}
    outputs = [d['untrusted_tool_result'] for d in users if 'untrusted_tool_result' in d]
    if not outputs:
        return {'decision': {'action': 'search_workspace', 'arguments': {
            'query': 'SFT 基线 cache_enabled 历史运行', 'strategy': 'bm25', 'top_k': 3}}}
    passages = outputs[-1]['passages']
    rows = []
    if case == 'report':
        evidence_ids = [key for key, value in passages.items() if 'SFT 基线' in value['quote']]
        rows.append(dict(statement='资料包含 SFT 基线。', verdict='supported', basis='documented_fact',
                         explanation='合成资料明确列出了 SFT 基线。', evidence_ids=evidence_ids))
    rows.append(dict(statement='配置足以证明历史运行启用了缓存。' if case == 'report' else '本次 GPU 总费用可以确定。',
                     verdict='insufficient', basis='historical_observation', explanation='没有对应记录，无法确定。', evidence_ids=[]))
    return {'decision': {'action': 'verify_and_submit_report', 'arguments': {'assessments': rows}}}


def serve_fixture(output, case, port):
    output.parent.mkdir(parents=True, exist_ok=True)
    log = output.open('x')
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            body = canonical(dict(fixture='round19b-scripted-not-an-llm', external_model_calls=0, case=case))
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < length <= 262144:
                self.send_error(400); return
            payload = _strict_generation_json(self.rfile.read(length))
            with lock:
                log.write(json.dumps(dict(request=payload, scripted=True, synthetic_usage=True)) + '\n'); log.flush()
            if case == 'timeout': time.sleep(3)
            content = fixture_content(payload, case)
            body = canonical(dict(choices=[dict(message=dict(role='assistant', content=json.dumps(content)), finish_reason='stop')],
                                  usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)))
            try:
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    try: server.serve_forever()
    finally: server.server_close(); log.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['prepare', 'run', 'fixture'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base-url', default='http://127.0.0.1:8086')
    p.add_argument('--case', choices=['report', 'insufficient', 'timeout'], default='report')
    p.add_argument('--evidence-kind', choices=['local_scripted', 'real_private_model'])
    p.add_argument('--gateway-plan-sha256')
    p.add_argument('--port', type=int, default=8771)
    args = p.parse_args()
    if args.mode == 'fixture': return serve_fixture(args.output, args.case, args.port)
    if args.mode == 'run':
        if not args.evidence_kind: p.error('run requires explicit --evidence-kind')
        result = run(args.output, args.evidence_kind)
    else: result = prepare(args.output, args.base_url, args.case, args.gateway_plan_sha256)
    print(json.dumps({k: v for k, v in result.items() if k not in {'checks'}}, ensure_ascii=False))
    return 0 if result.get('passed', True) else 1


if __name__ == '__main__':
    sys.exit(main())
