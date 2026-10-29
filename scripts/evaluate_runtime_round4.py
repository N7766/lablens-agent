#!/usr/bin/env python3
"""Actual JVM/Python review timing with a scripted, loopback-only provider.

Two transport controls, not model inference, semantic evaluation or load testing.
Never retries a task/provider call. All observations and failures are retained.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import threading
import time
from urllib.parse import urlparse

import httpx

from evaluate_workspace_agent_fault import Experiment, ROOT, now, sha

SYNTHETIC_USAGE = dict(prompt_tokens=13, completion_tokens=7, total_tokens=20)
AGENT_BUDGET = 6
REVIEW_DELAY = 8
PROTECTED_PORTS = {8081, 8083, 8765, 8766, 8767, 6380}

# The actual Python application runs with its normal HTTP clients and Agent.
# Only outbound socket destinations are restricted in this controlled process.
LOOPBACK_WORKER = """
import ipaddress, os, socket
original_connect = socket.socket.connect
allowed_ports = {int(os.environ['LABLENS_RUNTIME_TOOL_PORT']), int(os.environ['LABLENS_RUNTIME_PROVIDER_PORT'])}
def local_connect(self, address):
    if isinstance(address, tuple):
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback and address[1] in allowed_ports
        except ValueError:
            allowed = False
        if not allowed:
            raise OSError('Controlled runtime experiment prohibits non-allowlisted outbound sockets')
    else:
        raise OSError('Controlled runtime experiment prohibits outbound non-IP sockets')
    return original_connect(self, address)
socket.socket.connect = local_connect
import uvicorn
uvicorn.run('lablens.api:app', host='127.0.0.1', port=int(os.environ['LABLENS_RUNTIME_TOOL_PORT']))
"""


def fixture_reply(payload):
    """Return exactly the selected control sequence, with no model inference."""
    name = payload['response_format']['json_schema']['name']
    if name == 'evidence_assessments':
        data = json.loads(payload['messages'][-1]['content'])
        return 'review', data['untrusted_draft']
    if name != 'workspace_action':
        raise ValueError('Unexpected response schema in local fixture')
    outputs = []
    for message in payload['messages']:
        if message.get('role') != 'user':
            continue
        try:
            value = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and 'untrusted_tool_result' in value:
            outputs.append(value['untrusted_tool_result'])
    if not outputs:
        action = 'search_workspace'
        arguments = dict(query='synthetic accuracy', strategy='bm25', top_k=1)
    else:
        action = 'verify_and_submit_report'
        arguments = dict(assessments=[dict(statement='The measured accuracy was 86 percent.',
            verdict='supported', explanation='The supplied local fixture documents 86 percent.',
            basis='historical_observation', evidence_ids=list(outputs[-1]['passages']))])
    return 'decision', dict(decision=dict(action=action, arguments=arguments))


class RuntimeProvider:
    def __init__(self, port):
        self.case = 'not-started'
        self.events = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                payload = json.loads(raw)
                event = dict(case=owner.case, path=self.path, received_at=now(),
                    request_body_bytes=len(raw), request=payload, usage_scope='synthetic transport fixture')
                owner.events.append(event)
                try:
                    if self.path != '/v1/chat/completions':
                        raise ValueError('Unexpected local provider path')
                    phase, reply = fixture_reply(payload)
                    event['phase'] = phase
                    if owner.case == 'review_total_timeout' and phase == 'review':
                        time.sleep(REVIEW_DELAY)
                    response = dict(choices=[dict(finish_reason='stop', message=dict(role='assistant',
                        content=json.dumps(reply)))], usage=SYNTHETIC_USAGE)
                    code = 200
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    event['fixture_error'] = type(exc).__name__
                    response, code = {'error': 'Unexpected local control request'}, 400
                encoded = json.dumps(response).encode()
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    event['response_written'] = True
                except (BrokenPipeError, ConnectionResetError):
                    event['response_written'] = False
                finally:
                    event['finished_at'] = now()

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        until = time.monotonic() + REVIEW_DELAY + 2
        while any('finished_at' not in event for event in self.events) and time.monotonic() < until:
            time.sleep(.05)


def timing_checks(trace, provider_events):
    checks = {}
    for prefix, key in [('stage', 'stage_id'), ('request', 'request_id')]:
        start_events = [event for event in trace if event.get('event') == prefix + '_started']
        end_events = [event for event in trace if event.get('event') == prefix + '_finished']
        starts = {event[key]: event for event in start_events}
        ends = {event[key]: event for event in end_events}
        paired = bool(starts) and starts.keys() == ends.keys()
        checks[prefix + '_unique_pairs'] = paired and len(starts) == len(start_events) == len(end_events)
        checks[prefix + '_monotonic_durations'] = paired and all(
            ends[key]['elapsed_ms'] >= start['elapsed_ms'] and ends[key]['duration_ms'] >= 0
            and 0 <= ends[key]['remaining_ms'] <= start['remaining_ms'] <= AGENT_BUDGET * 1000
            for key, start in starts.items())
    requests = [event for event in trace if event.get('event') == 'request_finished']
    checks['three_provider_observations'] = len(requests) == len(provider_events) == 3
    checks['provider_body_bytes_match_transport'] = bool(requests) and (
        [event['request_body_bytes'] for event in requests] ==
        [event['request_body_bytes'] for event in provider_events])
    checks['request_phases_match_transport'] = [event['phase'] for event in requests] == [
        event.get('phase') for event in provider_events] == ['decision', 'decision', 'review']
    checks['request_timeouts_bounded'] = bool(requests) and all(
        0 < event['timeout_ms'] <= AGENT_BUDGET * 1000 for event in requests)
    stages = [event for event in trace if event.get('event') == 'stage_finished']
    by_id = {event['stage_id']: event for event in stages}
    reviews = [event for event in stages if event['phase'] == 'review']
    checks['review_nested_in_report_tool'] = len(reviews) == 1 and (
        by_id.get(reviews[0]['parent_stage_id'], {}).get('name') == 'verify_and_submit_report')
    searches = [event for event in stages if event.get('name') == 'search_workspace']
    checks['tool_http_metadata_saved'] = len(searches) == 1 and searches[0].get('request_body_bytes', 0) > 0 and (
        0 < searches[0].get('timeout_ms', 0) <= AGENT_BUDGET * 1000)
    telemetry = [event for event in trace if event.get('event', '').startswith(('stage_', 'request_'))]
    text = json.dumps(telemetry)
    checks['timing_events_payload_free'] = all(value not in text for value in (
        'Authorization', 'local-fixture-not-a-secret', '"messages"', 'The supplied local fixture'))
    return checks, requests, reviews


class RuntimeExperiment(Experiment):
    def __init__(self, args):
        super().__init__(args)
        self.database = 'lablens_round4_runtime_' + self.run_id
        self.summary.update(database=self.database,
            scope='Actual JVM/MySQL/Redis/Python with scripted loopback provider; no semantic or load evaluation.',
            configuration=dict(agent_total_seconds=AGENT_BUDGET, review_reserve_seconds=AGENT_BUDGET / 3,
                delayed_review_seconds=REVIEW_DELAY, provider_timeout_seconds=20, workers=1,
                worker_timeout_seconds=15, lease_seconds=30, automatic_provider_retries=0),
            limitations=['All provider responses and token counts are synthetic transport controls, not inference.',
                'The two cases establish local trace durability and review deadline handling, not throughput or quality.',
                'A timed-out remote response may still complete; acceptance and billing stay unknown to the client.',
                'Stage/request durations are nested and must not be added as disjoint elapsed time.'])
        self.inventory = sorted(set([Path(__file__).resolve(), ROOT / 'scripts/evaluate_workspace_agent_fault.py']
            + list((ROOT / 'lablens').glob('*.py'))
            + list((ROOT / 'services/workspace/src/main').rglob('*.java'))
            + list((ROOT / 'services/workspace/src/main/resources/db/migration').glob('*.sql'))
            + [ROOT / 'services/workspace/src/main/resources/application.properties']))
        self.summary['source_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in self.inventory}

    def prepare(self):
        ports = (self.args.jvm_port, self.args.python_port, self.args.provider_port, self.args.redis_port)
        if len(set(ports)) != len(ports) or PROTECTED_PORTS.intersection(ports):
            raise RuntimeError('Isolated, distinct ports are required; main/parent QA ports are protected')
        for port in ports:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        original_url = self.env['LABLENS_DB_URL']
        if urlparse(original_url.removeprefix('jdbc:')).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('Controlled experiment requires loopback MySQL')
        self.frozen_jar = self.private / 'workspace.jar'
        shutil.copyfile(self.args.jar, self.frozen_jar)
        self.summary['execution_jar'] = dict(source=str(self.args.jar.relative_to(ROOT)),
            immutable_copy=str(self.frozen_jar.relative_to(ROOT)), sha256=sha(self.frozen_jar))
        self.sql(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4; GRANT ALL ON `{self.database}`.* TO 'lablens'@'127.0.0.1';")
        self.env['LABLENS_DB_URL'] = re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+', r'\g<1>' + self.database, original_url)
        if self.database not in self.env['LABLENS_DB_URL']:
            raise RuntimeError('Could not select isolated database')
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            self.env.pop(key, None)
        self.env.update(NO_PROXY='127.0.0.1,localhost,::1', no_proxy='127.0.0.1,localhost,::1',
            LABLENS_WORKSPACE_BIND='127.0.0.1', LABLENS_WORKSPACE_PORT=str(self.args.jvm_port),
            LABLENS_REDIS_HOST='127.0.0.1', LABLENS_REDIS_PORT=str(self.args.redis_port), LABLENS_REDIS_PASSWORD='',
            LABLENS_WORKER_BASE_URL=f'http://127.0.0.1:{self.args.python_port}', LABLENS_WORKERS='1',
            LABLENS_TASK_LEASE_SECONDS='30', LABLENS_WORKER_TIMEOUT_SECONDS='15', LABLENS_RATE_LIMIT='20',
            LABLENS_CACHE_NAMESPACE='runtime-fault-' + self.run_id, LABLENS_AGENT_TOTAL_SECONDS=str(AGENT_BUDGET),
            LABLENS_AGENT_TOOL_BASE_URL=f'http://127.0.0.1:{self.args.python_port}',
            LABLENS_CONTEXT_WINDOW_TOKENS='65536', LABLENS_RUNTIME_TOOL_PORT=str(self.args.python_port),
            LABLENS_RUNTIME_PROVIDER_PORT=str(self.args.provider_port),
            LLM_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', LLM_MODEL='controlled-runtime-fixture',
            LLM_API_KEY='local-fixture-not-a-secret', LLM_TIMEOUT_SECONDS='20',
            EMBEDDING_BASE_URL=f'http://127.0.0.1:{self.args.provider_port}/v1', EMBEDDING_MODEL='unused-controlled-fixture',
            EMBEDDING_API_KEY='local-fixture-not-a-secret', RAG_TIMEOUT_SECONDS='20', RAG_TOTAL_SECONDS='60')
        self.summary['provider_origins'] = dict(generation=self.env['LLM_BASE_URL'], embedding=self.env['EMBEDDING_BASE_URL'])
        self.summary['network_guard'] = 'Python worker outbound IP socket connects restricted to own loopback tool/provider ports; proxy settings removed; JVM dependencies loopback.'
        self.provider = RuntimeProvider(self.args.provider_port)
        self.provider.start()
        self.start('redis', [self.args.redis, '--bind', '127.0.0.1', '--port', str(self.args.redis_port),
            '--dir', str(self.private), '--save', '', '--appendonly', 'no'])
        self.start('python', [sys.executable, '-c', LOOPBACK_WORKER])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.python_port}/health').status_code == 200)
        self.start('jvm', [self.args.java, '-jar', str(self.frozen_jar)])
        self.wait(lambda: self.http.get(f'http://127.0.0.1:{self.args.jvm_port}/api/system/status').status_code == 200, seconds=45)
        self.summary['initial_system_status'] = self.request('GET', '/api/system/status')

    def run_case(self, name):
        self.provider.case = name
        workspace = self.request('POST', '/api/workspaces', expected=201,
            json={'name': 'Round4 runtime control: ' + name})['id']
        self.request('PUT', f'/api/workspaces/{workspace}/documents/control', json=dict(
            title='Synthetic accuracy observation', text='The measured accuracy was 86 percent. This is a synthetic transport fixture.',
            source='synthetic:runtime-round4'))
        revision = self.request('GET', f'/api/workspaces/{workspace}/documents')['revision']
        request = dict(question='What accuracy does the synthetic observation report?', strategy='bm25',
            mode='agent', top_k=1, approved_revision=revision)
        created = self.request('POST', f'/api/workspaces/{workspace}/tasks', expected=202,
            headers={'Idempotency-Key': name}, json=request)
        tid = created['id']
        path = f'/api/workspaces/{workspace}/tasks/{tid}'
        def terminal():
            task = self.request('GET', path)
            return task if task['status'] in {'SUCCEEDED', 'FAILED'} else None
        final = self.wait(terminal, seconds=20)
        self.wait(lambda: all('finished_at' in event for event in self.provider.events if event['case'] == name), seconds=12)
        # Let a late response settle; task replay below must not dispatch again.
        time.sleep(.2)
        refreshed = self.request('GET', path)
        replay = self.request('POST', f'/api/workspaces/{workspace}/tasks',
            headers={'Idempotency-Key': name}, json=request)
        time.sleep(.3)
        db_row = json.loads(self.sql("SELECT JSON_OBJECT('id',id,'status',status,'error_code',error_code,"
            "'execution_usage',execution_usage_json,'result',result_json,'error_result',error_result_json) "
            f"FROM tasks WHERE id='{tid}';", self.database))
        events = [event for event in self.provider.events if event['case'] == name]
        result = final.get('result') or final.get('error_result') or {}
        agent = result.get('agent') or {}
        trace = agent.get('trace') or []
        usage = final.get('execution_usage') or {}
        checks, requests, reviews = timing_checks(trace, events)
        checks.update(telemetry_version=agent.get('telemetry_version') == 'runtime-stages-v1',
            source_snapshot_bound=agent.get('snapshot_id') == workspace + ':' + str(revision),
            document_hash_saved=len(agent.get('documents_sha256', '')) == 64,
            declared_total_budget=agent.get('budget_seconds') == AGENT_BUDGET,
            declared_review_reserve=agent.get('review_reserve_seconds') == AGENT_BUDGET / 3,
            two_model_decisions=[event.get('tools') for event in trace if event.get('event') == 'model_decision'] == [
                ['search_workspace'], ['verify_and_submit_report']],
            known_decision_usage_preserved=len(requests) >= 2 and all(
                event.get('usage') == SYNTHETIC_USAGE and event.get('usage_known') is True for event in requests[:2]),
            three_calls_no_auto_retry=usage.get('model_calls') == len(events) == 3,
            no_embedding_calls=usage.get('embedding_requests') == 0 and all(event['path'] == '/v1/chat/completions' for event in events),
            provider_fixture_valid=all('fixture_error' not in event for event in events),
            refreshed_identical=refreshed == final,
            database_usage_matches_api=db_row['execution_usage'] == usage,
            database_result_matches_api=db_row['result'] == final.get('result'),
            database_failure_matches_api=db_row['error_result'] == final.get('error_result'),
            idempotent_replay_same_task=replay.get('reused') is True and replay['id'] == tid and replay['status'] == final['status'])
        review = requests[-1] if requests else {}
        if name == 'fast_success':
            checks.update(task_succeeded=final['status'] == 'SUCCEEDED' and result.get('status') == 'answered',
                all_usage_known=usage.get('provider_usage_unknown') is False and usage.get('generation_tokens') == {
                    key: value * 3 for key, value in SYNTHETIC_USAGE.items()},
                review_returned=review.get('outcome') == 'returned' and review.get('response_received') is True,
                completed_review_recorded=any(event.get('event') == 'review_completed' for event in trace))
        else:
            checks.update(task_failed_timeout=final['status'] == 'FAILED' and final.get('error_code') == 'agent_timeout',
                no_published_report=final.get('result') is None and not result.get('answer'),
                unknown_total_usage=usage.get('provider_usage_unknown') is True and usage.get('generation_tokens') == {
                    key: None for key in SYNTHETIC_USAGE},
                review_request_timeout=review.get('outcome') == 'timeout',
                review_response_unknown=review.get('response_received') is False and review.get('provider_acceptance') == 'unknown',
                review_usage_unknown=review.get('usage_known') is False and review.get('usage') == {
                    key: None for key in SYNTHETIC_USAGE},
                review_stage_timeout=len(reviews) == 1 and reviews[0].get('outcome') == 'timeout',
                no_successful_review=not any(event.get('event') == 'review_completed' for event in trace))
        case = dict(name=name, passed=all(checks.values()), checks=checks, workspace_id=workspace,
            task_id=tid, final=final, refreshed=refreshed, replay=replay, database_row=db_row, provider_events=events)
        self.summary['cases'].append(case)
        self.save()
        print(json.dumps(dict(case=name, passed=case['passed'], checks=len(checks),
            failed_checks=[key for key, value in checks.items() if not value])), flush=True)
        if not case['passed']:
            raise AssertionError('Failed checks: ' + ', '.join(key for key, value in checks.items() if not value))

    def cleanup(self):
        super().cleanup()
        self.summary['execution_jar_unchanged'] = (
            sha(self.frozen_jar) == self.summary['execution_jar']['sha256']) if (
                hasattr(self, 'frozen_jar') and self.frozen_jar.exists() and 'execution_jar' in self.summary) else None
        self.summary['cleanup']['provider_handlers_finished'] = all('finished_at' in event for event in self.provider.events) if self.provider else True
        required = [self.summary['source_unchanged'], self.summary['execution_jar_unchanged'],
            self.summary['cleanup']['all_owned_processes_stopped'],
            self.summary['cleanup']['controlled_provider_stopped'],
            self.summary['cleanup']['provider_handlers_finished']]
        if self.summary['status'] == 'passed' and not all(required):
            self.summary['status'] = 'failed'
            self.summary['failure'] = dict(type='IntegrityOrCleanupFailure', message='Source, immutable JAR or owned process cleanup verification failed.')
        self.summary['check_counts'] = dict(total=sum(len(case['checks']) for case in self.summary['cases']),
            passed=sum(sum(case['checks'].values()) for case in self.summary['cases']))
        self.save()
        lines = ['# Round4 runtime transport controls', '',
            'Actual JVM → actual Python Agent → scripted local provider. No external model/embedding requests.', '',
            'The provider returns exactly two decisions (search, verify/report) followed by a review. Its token counts are synthetic. This is transport and durability evidence, not model quality, inference performance, or load testing.', '',
            f"Status: {self.summary['status']}; checks: {self.summary['check_counts']['passed']}/{self.summary['check_counts']['total']}.", '',
            '| Case | Passed |', '| --- | --- |']
        lines += [f"| {case['name']} | {case['passed']} |" for case in self.summary['cases']]
        lines += ['', 'A 6-second Agent budget retains a 2-second review reserve. The timeout fixture delays only the review response by 8 seconds. Timing assertions concern pairing, ordering and recorded budgets; no machine-specific minimum latency is required.', '',
            'Evidence: [summary](summary.json), [raw HTTP observations](http-observations.json), [raw scripted-provider events](provider-events.json). Sources and the executed immutable JAR are fingerprinted. Main and parent QA ports are protected. The isolated database is retained; owned processes are stopped.', '',
            'The client correctly records unknown review acceptance/usage after timeout even though this controlled provider independently records receiving the request. Nested stages and request durations must not be summed.']
        (self.out / 'README.md').write_text('\n'.join(lines) + '\n')


def self_test():
    decision_payload = dict(response_format=dict(json_schema=dict(name='workspace_action')),
        messages=[dict(role='user', content=json.dumps({'question': 'accuracy'}))])
    phase, first = fixture_reply(decision_payload)
    assert phase == 'decision' and first['decision']['action'] == 'search_workspace'
    decision_payload['messages'].append(dict(role='user', content=json.dumps({
        'untrusted_tool_result': {'passages': {'controlled-evidence-id': {'quote': '86 percent'}}}})))
    phase, second = fixture_reply(decision_payload)
    assert phase == 'decision' and second['decision']['action'] == 'verify_and_submit_report'
    assert second['decision']['arguments']['assessments'][0]['evidence_ids'] == ['controlled-evidence-id']
    review_payload = dict(response_format=dict(json_schema=dict(name='evidence_assessments')),
        messages=[dict(role='user', content=json.dumps({'untrusted_draft': second['decision']['arguments']}))])
    assert fixture_reply(review_payload) == ('review', second['decision']['arguments'])
    assert all(port in PROTECTED_PORTS for port in (8081, 8765, 8083, 8767))
    assert AGENT_BUDGET > AGENT_BUDGET / 3 and REVIEW_DELAY > AGENT_BUDGET
    assert len(httpx.Request('POST', 'http://127.0.0.1/', json=review_payload).content) > 0
    compile(LOOPBACK_WORKER, '<isolated-worker-launcher>', 'exec')
    print('8 offline harness checks passed; no HTTP requests, services, files or model inference.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/services.env'))
    parser.add_argument('--mysql-socket', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock'))
    parser.add_argument('--mysql', default='/opt/homebrew/opt/mysql/bin/mysql')
    parser.add_argument('--java', default='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java')
    parser.add_argument('--redis', default='/tmp/lablens-tooling/redis-8.2.2/src/redis-server')
    parser.add_argument('--jar', type=Path, default=ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/conversation-round4/runtime-fault')
    parser.add_argument('--jvm-port', type=int, default=8084)
    parser.add_argument('--python-port', type=int, default=8768)
    parser.add_argument('--provider-port', type=int, default=8770)
    parser.add_argument('--redis-port', type=int, default=6392)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    args.jar = args.jar.resolve()
    experiment = RuntimeExperiment(args)
    try:
        experiment.prepare()
        experiment.run_case('fast_success')
        experiment.run_case('review_total_timeout')
        experiment.summary['status'] = 'passed'
    except Exception as exc:
        experiment.summary['status'] = 'failed'
        experiment.summary['failure'] = dict(type=type(exc).__name__,
            message=str(exc) if isinstance(exc, AssertionError) else 'Inspect retained private process logs.')
    finally:
        experiment.cleanup()
    print(json.dumps(dict(status=experiment.summary['status'], cases=len(experiment.summary['cases']),
        report=str(experiment.out.relative_to(ROOT) / 'summary.json'))), flush=True)
    return 0 if experiment.summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
