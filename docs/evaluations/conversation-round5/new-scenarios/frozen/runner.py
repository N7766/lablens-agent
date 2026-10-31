#!/usr/bin/env python3
"""Frozen, no-retry public-API runner. Does not read answer semantics."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / 'frozen'
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED'}
MAX_TASKS = 4
MAX_CALLS_PER_TASK = 7
MAX_CALLS_TOTAL = 28


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def verify_freeze(expected):
    manifest_path = ROOT / 'freeze-manifest.json'
    if sha(manifest_path) != expected:
        raise RuntimeError('Freeze manifest identity mismatch')
    manifest = json.loads(manifest_path.read_text())
    for relative, digest in manifest['files'].items():
        if sha(ROOT / relative) != digest:
            raise RuntimeError('Frozen file mismatch: ' + relative)
    return manifest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


class API:
    def __init__(self, base, run):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != 'http' or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('QA URL must be plain loopback HTTP origin')
        if not parsed.hostname or not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError('QA host must be explicit loopback IP')
        self.base = base.rstrip('/')
        self.run = run
        self.index = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, payload=None, key=None, timeout=20):
        self.index += 1
        tag = f'{self.index:04d}-{method.lower()}'
        prefix = self.run / 'raw' / tag
        headers = {'Accept': 'application/json'}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = 'application/json'
            prefix.with_suffix('.request-body.bin').write_bytes(body)
        if key:
            headers['Idempotency-Key'] = key
        meta = {'sequence': self.index, 'started_at': utc(), 'method': method, 'url': self.base + path,
                'request_headers': headers, 'request_json': payload, 'timeout_seconds': timeout,
                'retry_count': 0}
        start = time.monotonic()
        status, raw, response_headers = None, b'', {}
        try:
            req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
            with self.opener.open(req, timeout=max(0.1, timeout)) as response:
                status = response.status
                raw = response.read()
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
            response_headers = dict(error.headers.items()) if error.headers else {}
        except Exception as error:
            meta['transport_error'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            prefix.with_suffix('.response-body.bin').write_bytes(raw)
            meta.update({'finished_at': utc(), 'elapsed_seconds': time.monotonic() - start, 'status': status,
                         'response_headers': {k: v for k, v in response_headers.items()
                                              if k.lower() in {'content-type', 'date', 'content-length'}},
                         'response_body_file': prefix.with_suffix('.response-body.bin').name})
            dump(prefix.with_suffix('.http.json'), meta)
        if status is None or not 200 <= status < 300:
            raise RuntimeError(f'HTTP failure {method} {path}: {status}')
        if not raw:
            return None
        return json.loads(raw)


def path_id(value):
    return urllib.parse.quote(str(value), safe='')


def valid_previous(conversation, task_id):
    for turn in conversation.get('turns', []):
        if turn.get('task_id') == task_id and turn.get('status') == 'SUCCEEDED':
            return isinstance(turn.get('answer'), str) and bool(turn['answer'].strip())
    return False


def usage_from(task):
    usage = task.get('execution_usage')
    if isinstance(usage, dict):
        return usage, 'execution_usage'
    for field in ('result', 'error_result'):
        nested = task.get(field)
        if isinstance(nested, dict) and isinstance(nested.get('usage'), dict):
            return nested['usage'], field + '.usage'
    return None, None


def seal(run):
    files = {str(p.relative_to(run)): sha(p) for p in sorted(run.rglob('*'))
             if p.is_file() and p.name != 'raw-seal.json'}
    manifest = {'schema_version': 1, 'sealed_at': utc(), 'semantic_reading_started': False, 'files': files}
    dump(run / 'raw-seal.json', manifest)
    return sha(run / 'raw-seal.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--go', required=True, choices=['GO'])
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--freeze-sha', required=True)
    parser.add_argument('--attest-budget', required=True,
                        choices=['6-decisions+1-review;75s;no-model-retries;bm25-no-embedding'])
    parser.add_argument('--run-label', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', args.candidate_sha):
        parser.error('Candidate SHA must be an immutable 40-64 hex identity')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', args.run_label):
        parser.error('run-label must be a safe new directory name')
    verify_freeze(args.freeze_sha)
    data = json.loads((FROZEN / 'dataset.json').read_text())
    run = ROOT / 'runs' / args.run_label
    run.mkdir(parents=True, exist_ok=False)
    (run / 'raw').mkdir()
    dump(run / 'authorization.json', {'authorized_at': utc(), 'go': args.go, 'qa_base_url': args.base_url,
         'candidate_sha': args.candidate_sha, 'freeze_manifest_sha256': args.freeze_sha,
         'operator_budget_attestation': args.attest_budget, 'planned_tasks': MAX_TASKS,
         'max_decision_calls_per_task': 6, 'max_review_calls_per_task': 1,
         'max_model_calls_per_task': MAX_CALLS_PER_TASK, 'max_model_calls_total': MAX_CALLS_TOTAL,
         'service_boundary_seconds': 75, 'client_observation_seconds': 200,
         'post_retries': 0, 'model_retries': 0, 'same_ai_author_and_scorer': True})
    outcomes = {turn['id']: {'turn_id': turn['id'], 'scenario_id': scenario['id'],
                'depends_on': turn['depends_on'], 'status': 'not_submitted', 'task_id': None}
                for scenario in data['scenarios'] for turn in scenario['turns']}
    submitted, known_calls, unknown_usage_tasks = 0, 0, []
    fatal, active_task = None, None
    api = API(args.base_url, run)
    try:
        dump(run / 'system-status-before.json', api.request('GET', '/api/system/status'))
        for scenario in data['scenarios']:
            workspace = api.request('POST', '/api/workspaces', {'name': 'round5-' + scenario['id'] + '-' + args.run_label})
            wid = workspace['id']
            wp = '/api/workspaces/' + path_id(wid)
            dump(run / (scenario['id'] + '-workspace.json'), workspace)
            for document in scenario['documents']:
                api.request('PUT', wp + '/documents/' + path_id(document['id']),
                            {k: document[k] for k in ('title', 'text', 'source')})
            documents = api.request('GET', wp + '/documents')
            dump(run / (scenario['id'] + '-documents.json'), documents)
            conversation = api.request('POST', wp + '/conversations', scenario['conversation'])
            cid = conversation['id']
            cp = wp + '/conversations/' + path_id(cid)
            dump(run / (scenario['id'] + '-conversation-created.json'), conversation)
            for turn in scenario['turns']:
                current = outcomes[turn['id']]
                current.update({'workspace_id': wid, 'conversation_id': cid})
                conversation = api.request('GET', cp)
                if turn['depends_on']:
                    previous = outcomes[turn['depends_on']]
                    if previous['status'] != 'SUCCEEDED' or not valid_previous(conversation, previous['task_id']):
                        current['status'] = 'blocked_no_valid_prior_response'
                        current['blocking_predecessor_status'] = previous['status']
                        continue
                documents = api.request('GET', wp + '/documents')
                payload = {'question': turn['question'], 'strategy': 'bm25', 'mode': 'agent', 'top_k': 5,
                           'approved_revision': documents['revision'],
                           'expected_conversation_revision': conversation['revision']}
                if submitted >= MAX_TASKS or known_calls > MAX_CALLS_TOTAL - MAX_CALLS_PER_TASK:
                    raise RuntimeError('Pre-submission budget guard')
                key = 'r5new-' + turn['id'] + '-' + uuid.uuid4().hex
                current.update({'status': 'submission_attempted', 'idempotency_key': key, 'request': payload})
                submitted += 1
                dump(run / 'outcomes-checkpoint.json', outcomes)
                try:
                    task = api.request('POST', cp + '/turns', payload, key=key)
                except Exception:
                    current['status'] = 'uncertain_submission_or_http_failure'
                    raise
                tid = task['id']
                current['task_id'] = tid
                current['status'] = task.get('status', 'submitted_status_unknown')
                task_path = wp + '/tasks/' + path_id(tid)
                active_task = task_path
                dump(run / (turn['id'] + '-submitted-task.json'), task)
                observation_start = time.monotonic()
                deadline = observation_start + 200
                while task.get('status') not in TERMINAL:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        current['status'] = 'client_observation_timeout'
                        current['observation_seconds'] = time.monotonic() - observation_start
                        try:
                            dump(run / (turn['id'] + '-cancel-response.json'), api.request('POST', task_path + '/cancel'))
                            current['cancel_control_sent'] = True
                        finally:
                            active_task = None
                        raise RuntimeError('Client 200-second observation window elapsed; later submissions stopped')
                    time.sleep(min(2, max(0, remaining)))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        continue
                    task = api.request('GET', task_path, timeout=min(20, remaining))
                active_task = None
                current.update({'status': task['status'], 'observation_seconds': time.monotonic() - observation_start,
                                'task_file': turn['id'] + '-final-task.json'})
                dump(run / (turn['id'] + '-final-task.json'), task)
                conversation = api.request('GET', cp)
                dump(run / (turn['id'] + '-conversation.json'), conversation)
                current['valid_saved_response'] = valid_previous(conversation, tid)
                usage, origin = usage_from(task)
                current.update({'execution_usage': usage, 'usage_origin': origin,
                                'task_usage_unknown': task.get('usage_unknown')})
                if not isinstance(usage, dict) or type(usage.get('model_calls')) is not int or usage['model_calls'] < 0:
                    unknown_usage_tasks.append(turn['id'])
                    raise RuntimeError('Model call count unknown; no further submissions')
                calls = usage['model_calls']
                known_calls += calls
                if calls > MAX_CALLS_PER_TASK or known_calls > MAX_CALLS_TOTAL:
                    raise RuntimeError('Reported model call budget exceeded')
                for embedding_field in ('embedding_requests', 'embedded_inputs'):
                    if usage.get(embedding_field) not in (0, None):
                        raise RuntimeError('Unexpected provider embedding activity under BM25')
                dump(run / 'outcomes-checkpoint.json', outcomes)
        dump(run / 'system-status-after.json', api.request('GET', '/api/system/status'))
    except BaseException as error:
        fatal = {'type': type(error).__name__, 'message': str(error), 'at': utc()}
        if active_task:
            try:
                dump(run / 'interrupt-cancel-response.json', api.request('POST', active_task + '/cancel'))
            except BaseException as cancellation_error:
                fatal['cancellation_error'] = {'type': type(cancellation_error).__name__, 'message': str(cancellation_error)}
        for turn_id, current in outcomes.items():
            if current['status'] == 'not_submitted':
                predecessor = outcomes.get(current['depends_on'])
                current['status'] = ('blocked_no_valid_prior_response' if predecessor and
                    (predecessor['status'] != 'SUCCEEDED' or not predecessor.get('valid_saved_response'))
                    else 'blocked_run_stopped')
    finally:
        for turn_id, current in outcomes.items():
            if current.get('idempotency_key') and not isinstance(current.get('execution_usage'), dict):
                if turn_id not in unknown_usage_tasks:
                    unknown_usage_tasks.append(turn_id)
        summary = {'schema_version': 1, 'completed_at': utc(), 'candidate_sha': args.candidate_sha,
                   'freeze_manifest_sha256': args.freeze_sha, 'planned_turns': 4,
                   'task_submission_attempts': submitted, 'known_model_calls': known_calls,
                   'unknown_usage_tasks': unknown_usage_tasks, 'max_model_calls': 28,
                   'fatal': fatal, 'turns': list(outcomes.values()), 'semantic_reading_started': False}
        dump(run / 'run-summary.json', summary)
        raw_identity = seal(run)
        print(json.dumps({'run_directory': str(run), 'raw_seal_sha256': raw_identity,
                          'planned_turns': 4, 'submitted': submitted, 'known_model_calls': known_calls,
                          'unknown_usage_tasks': unknown_usage_tasks,
                          'statuses': {k: v['status'] for k, v in outcomes.items()},
                          'fatal': fatal}, ensure_ascii=False, indent=2))
    return 1 if fatal else 0


if __name__ == '__main__':
    sys.exit(main())
