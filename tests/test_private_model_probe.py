"""Real loopback HTTP transport with scripted replies, never a real model/API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('private_model_probe', ROOT / 'scripts/verify_private_model.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
KEY = 'synthetic-private-model-key'


def review_content(protocol):
    replacement = dict(verdict='insufficient', basis='historical_observation',
                       explanation='没有历史运行日志；配置不能证明历史执行。', evidence_ids=[])
    if protocol == probe.DECISION_REVIEW_PROTOCOL:
        return dict(decisions=dict(a1=dict(action='retain'),
                                   a2=dict(action='replace', **replacement)))
    return dict(retained_assessment_ids=['a1'],
                assessments=[dict(assessment_id='a2', **replacement)])


def completion(payload):
    name = payload.get('response_format', {}).get('json_schema', {}).get('name')
    if name is None:
        content = '本地模型就绪'
    elif name == 'workspace_action':
        content = json.dumps(dict(decision=dict(action='search_workspace',
                           arguments=dict(query='SFT 基线和历史运行日志', strategy='bm25', top_k=3))))
    else:
        protocol, _ = probe.review_payload_protocol(payload)
        content = json.dumps(review_content(protocol))
    return dict(choices=[dict(finish_reason='stop', message=dict(role='assistant', content=content))],
                usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))


@pytest.fixture
def server(tmp_path, monkeypatch):
    state = dict(calls=[], behavior=lambda payload, index: (200, completion(payload)), headers={},
                 output=tmp_path / 'candidate', intent_verified=[])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append(dict(path=self.path, payload=payload,
                                       authorization=self.headers.get('Authorization')))
            index = len(state['calls'])
            paths = sorted((state['output'] / 'run').glob('*/intent.json'))
            state['intent_verified'].append(len(paths) == index and
                probe.read_json(paths[-1])['payload_sha256'] == probe.sha(probe.canonical(payload)))
            status, body = state['behavior'](payload, index)
            try:
                if status == 'trickle':
                    self.send_response(200)
                    self.send_header('Content-Length', '1000')
                    self.end_headers()
                    for _ in range(1000):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.02)
                    return
                if status == 'disconnect':
                    self.send_response(200)
                    self.send_header('Content-Length', '1000')
                    self.end_headers()
                    self.wfile.write(b'{"incomplete":true}')
                    self.wfile.flush()
                    self.close_connection = True
                    return
                if isinstance(status, tuple):
                    seconds, status = status
                    time.sleep(seconds)
                raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/must-not-follow')
                self.send_header('Content-Length', str(len(raw)))
                for name, value in state['headers'].items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    state['base_url'] = f'http://127.0.0.1:{httpd.server_port}/v1'
    state['profile_path'] = tmp_path / 'profile.json'
    state['profile_path'].write_bytes(probe.PROFILE_PATH.read_bytes())
    monkeypatch.setattr(probe, 'PROFILE_PATH', state['profile_path'])
    monkeypatch.setenv(probe.KEY_ENV, KEY)
    # If HTTPX trusts proxy variables, these intentionally broken URLs fail.
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy-user:private-proxy-password@127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy-user:private-proxy-password@127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://proxy-user:private-proxy-password@127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=1)


def frozen(server, **kwargs):
    return probe.freeze(server['output'], base_url=server['base_url'],
                        request_seconds=kwargs.get('request_seconds', 2),
                        total_seconds=kwargs.get('total_seconds', 10),
                        review_protocol=kwargs.get('review_protocol', probe.DEFAULT_REVIEW_PROTOCOL))


def verify_manifest(output):
    directory = output / 'run'
    manifest = probe.read_json(directory / 'manifest.json')
    assert manifest['frozen_plan_sha256'] == probe.sha((directory / 'frozen-plan.json').read_bytes())
    for name, digest in manifest['files'].items():
        assert probe.sha((directory / name).read_bytes()) == digest


def assert_no_secrets(output):
    for path in output.rglob('*'):
        if path.is_file():
            body = path.read_bytes()
            assert KEY.encode() not in body
            assert b'private-proxy-password' not in body


def test_default_freeze_is_offline_and_freezes_real_production_schemas(server, monkeypatch, capsys):
    monkeypatch.delenv(probe.KEY_ENV)
    assert probe.main(['--output', str(server['output']), '--base-url', server['base_url']]) == 0
    assert not server['calls']
    assert 'frozen_no_http' in capsys.readouterr().out
    plan = probe.verify_plan(server['output'])
    assert plan['review_protocol'] == probe.DEFAULT_REVIEW_PROTOCOL
    assert [row['payload']['max_tokens'] for row in plan['requests']] == [64, 1800, 2600]
    action = plan['requests'][1]['payload']['response_format']['json_schema']
    assert action['name'] == 'workspace_action'
    alternatives = action['schema']['properties']['decision']['anyOf']
    assert len(alternatives) == 4
    assert alternatives[0]['properties']['arguments']['properties']['top_k']['maximum'] == 3
    review = plan['requests'][2]['payload']
    assert review['response_format']['json_schema']['name'] == 'evidence_review_by_id'
    assert '$defs' in review['response_format']['json_schema']['schema']
    assert json.loads(review['messages'][1]['content'])['server_review_delta']['version'] == 'scoped-review-delta-v3'
    assert {'lablens/rag.py', 'lablens/model.py', 'lablens/workspace_agent.py',
            'scripts/verify_private_model.py'} <= plan['source_files'].keys()
    profile = plan['deployment_profile']
    assert profile['file_sha256'] == probe.sha(server['profile_path'].read_bytes())
    assert profile['expected_identity']['served_model_name'] == 'lablens-private'
    assert profile['expected_identity']['model_revision'] == probe.read_json(server['profile_path'])['model_revision']
    assert len(profile['expected_identity']['weight_files_expected_from_hf_metadata']) == 4
    assert profile['server_identity_verified'] is False
    assert_no_secrets(server['output'])


def test_three_real_loopback_requests_validate_production_contracts_and_seal(server):
    frozen(server)
    summary = probe.run(server['output'])
    assert summary['status'] == 'compatibility_passed'
    assert summary['dispatch_intents'] == 3
    assert summary['skipped_requests'] == 0
    assert summary['aggregate_usage']['total_tokens'] == 45
    assert summary['semantic_acceptance'] == 'not_evaluated'
    assert server['intent_verified'] == [True, True, True]
    assert all(call['path'] == '/v1/chat/completions' for call in server['calls'])
    assert all(call['authorization'] == 'Bearer ' + KEY for call in server['calls'])
    detail = probe.read_json(server['output'] / 'run/03-scoped_review/validation.json')
    assert detail['assessment_count'] == 2
    assert not detail['selected_evidence_checks']['semantic_entailment_verified']
    verify_manifest(server['output'])
    assert_no_secrets(server['output'])
    with pytest.raises(FileExistsError):
        probe.run(server['output'])
    assert len(server['calls']) == 3


@pytest.mark.parametrize('failure,index,expected', [
    ('unauthorized', 1, 'http_error'),
    ('schema_unsupported', 2, 'http_error'),
    ('redirect', 1, 'http_error'),
    ('length', 1, 'finish_length'),
    ('length', 2, 'finish_length'),
    ('length', 3, 'finish_length'),
    ('bad_outer_json', 2, 'malformed_response'),
    ('duplicate_outer_key', 2, 'malformed_response'),
    ('bad_content', 2, 'structured_contract_failed'),
    ('fenced_json', 2, 'structured_contract_failed'),
    ('unknown_action', 2, 'action_contract_failed'),
    ('bad_argument_type', 2, 'structured_contract_failed'),
    ('bad_strategy', 2, 'action_contract_failed'),
    ('empty_review', 3, 'structured_contract_failed'),
    ('missing_review_row', 3, 'structured_contract_failed'),
    ('unknown_evidence_id', 3, 'structured_contract_failed'),
    ('missing_usage', 1, 'usage_unknown'),
    ('inconsistent_usage', 2, 'usage_unknown'),
    ('boolean_usage', 1, 'usage_unknown'),
    ('output_budget', 1, 'provider_reported_output_budget_violation'),
    ('body_limit', 2, 'response_body_limit'),
])
def test_failure_stops_all_dependent_requests_without_fallback(server, failure, index, expected):
    def behavior(payload, current):
        body = completion(payload)
        if current != index:
            return 200, body
        if failure == 'unauthorized':
            return 401, {'error': 'Authorization: Bearer ' + KEY, 'api_key': KEY}
        if failure == 'schema_unsupported':
            assert 'response_format' in payload
            return 400, {'error': 'unsupported anyOf in response_format'}
        if failure == 'redirect':
            return 302, {}
        if failure == 'length':
            body['choices'][0]['finish_reason'] = 'length'
        elif failure == 'bad_outer_json':
            return 200, b'not-json'
        elif failure == 'duplicate_outer_key':
            return 200, b'{"choices":[],"choices":[]}'
        elif failure == 'body_limit':
            return 200, b'x' * (probe.MAX_BODY_BYTES + 100)
        elif failure == 'bad_content':
            body['choices'][0]['message']['content'] = '{invalid'
        elif failure == 'fenced_json':
            body['choices'][0]['message']['content'] = '```json\n' + body['choices'][0]['message']['content'] + '\n```'
        elif failure in {'unknown_action', 'bad_argument_type', 'bad_strategy'}:
            decision = json.loads(body['choices'][0]['message']['content'])
            if failure == 'unknown_action':
                decision['decision']['action'] = 'native_tool_fallback'
            elif failure == 'bad_argument_type':
                decision['decision']['arguments']['top_k'] = True
            else:
                decision['decision']['arguments']['strategy'] = 'dense'
            body['choices'][0]['message']['content'] = json.dumps(decision)
        elif failure in {'empty_review', 'missing_review_row', 'unknown_evidence_id'}:
            parsed = json.loads(body['choices'][0]['message']['content'])
            if failure == 'empty_review':
                parsed = {'retained_assessment_ids': [], 'assessments': []}
            elif failure == 'missing_review_row':
                parsed['assessments'] = []
            else:
                parsed['assessments'][0]['evidence_ids'] = ['invented-id']
            body['choices'][0]['message']['content'] = json.dumps(parsed)
        elif failure == 'missing_usage':
            del body['usage']
        elif failure == 'inconsistent_usage':
            body['usage']['total_tokens'] = 100
        elif failure == 'boolean_usage':
            body['usage']['prompt_tokens'] = True
        elif failure == 'output_budget':
            body['usage']['completion_tokens'] = 65
            body['usage']['total_tokens'] = 75
        return 200, body
    server['behavior'] = behavior
    frozen(server)
    summary = probe.run(server['output'])
    assert summary['status'] == 'blocked'
    assert len(server['calls']) == index
    assert summary['attempts'][-1]['status'] == expected
    assert summary['no_automatic_retry']
    if failure in {'missing_usage', 'inconsistent_usage', 'boolean_usage', 'body_limit'}:
        assert summary['aggregate_usage']['total_tokens'] is None
    if failure == 'length':
        assert summary['attempts'][-1]['usage']['status'] == 'provider_reported'
    verify_manifest(server['output'])
    assert_no_secrets(server['output'])


@pytest.mark.parametrize('style', ['headers_delayed', 'trickling_body'])
def test_absolute_request_deadline_stops_even_trickling_body(server, style):
    server['behavior'] = lambda payload, index: (('trickle', b'') if style == 'trickling_body'
                                                else ((.5, 200), completion(payload)))
    frozen(server, request_seconds=.12)
    started = time.monotonic()
    summary = probe.run(server['output'])
    assert time.monotonic() - started < 1
    assert len(server['calls']) == 1
    attempt = summary['attempts'][0]
    assert attempt['status'] == 'timeout_usage_unknown'
    assert attempt['outcome_unknown']
    assert attempt['usage']['total_tokens'] is None
    assert summary['skipped_requests'] == 2
    verify_manifest(server['output'])


def test_total_deadline_reduces_second_request_budget(server):
    server['behavior'] = lambda payload, index: ((.15, 200), completion(payload))
    frozen(server, request_seconds=1, total_seconds=.25)
    summary = probe.run(server['output'])
    assert summary['status'] == 'blocked'
    assert 1 <= len(server['calls']) <= 2
    assert summary['attempts'][-1]['status'] == 'timeout_usage_unknown'
    assert summary['elapsed_seconds'] < 1


@pytest.mark.parametrize('failure,expected', [('disconnect', 'transport_error_usage_unknown'),
                                            ('gzip', 'unsupported_content_encoding')])
def test_incomplete_or_encoded_body_cannot_claim_known_usage(server, failure, expected):
    if failure == 'disconnect':
        server['behavior'] = lambda payload, index: ('disconnect', b'')
    else:
        server['headers'] = {'Content-Encoding': 'gzip'}
    frozen(server)
    summary = probe.run(server['output'])
    assert len(server['calls']) == 1
    assert summary['attempts'][0]['status'] == expected
    assert summary['usage_status'] == 'unknown'
    assert summary['attempts'][0]['outcome_unknown']


def test_concurrent_run_claim_admits_exactly_one_execution(server):
    frozen(server)
    def attempt():
        try:
            return probe.run(server['output'])['status']
        except FileExistsError:
            return 'already_claimed'
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(statuses) == ['already_claimed', 'compatibility_passed']
    assert len(server['calls']) == 3


@pytest.mark.parametrize('change,state', [('replace', 'changed'), ('delete', 'unavailable')])
def test_mid_request_plan_change_cannot_rebind_verified_bytes_or_break_seal(server, change, state):
    frozen(server)
    path = server['output'] / 'plan.json'
    original = path.read_bytes()
    digest = probe.sha(original)
    def behavior(payload, index):
        if index == 1:
            if change == 'replace':
                path.write_text('{"unrelated_new_candidate":true}')
            else:
                path.unlink()
        return 200, completion(payload)
    server['behavior'] = behavior
    summary = probe.run(server['output'])
    assert summary['status'] == 'compatibility_passed'
    assert len(server['calls']) == 3
    assert summary['frozen_plan_sha256'] == digest
    assert summary['disk_plan_changed'] is True
    assert summary['disk_plan_state'] == state
    assert (server['output'] / 'run/frozen-plan.json').read_bytes() == original
    for intent in (server['output'] / 'run').glob('*/intent.json'):
        assert probe.read_json(intent)['frozen_plan_sha256'] == digest
    manifest = probe.read_json(server['output'] / 'run/manifest.json')
    assert manifest['frozen_plan_sha256'] == digest
    assert manifest['disk_plan_state'] == state
    verify_manifest(server['output'])


def test_evidence_write_delay_cannot_extend_total_dispatch_budget(server, monkeypatch):
    frozen(server, request_seconds=1, total_seconds=.03)
    original = probe.write_json
    def slow_intent(path, value):
        original(path, value)
        if path.name == 'intent.json':
            time.sleep(.05)
    monkeypatch.setattr(probe, 'write_json', slow_intent)
    summary = probe.run(server['output'])
    assert not server['calls']
    assert summary['status'] == 'total_deadline_exhausted'
    assert summary['attempts'][0]['transport_entered'] is False


@pytest.mark.parametrize('base', [
    'https://api.example.com/v1', 'http://example.com:8080/v1', 'http://localhost:8080/v1',
    'http://127.0.0.1/v1', 'http://127.0.0.1:8080/v1/', 'http://127.0.0.1:8080/v1?key=secret',
    'http://user:password@127.0.0.1:8080/v1', 'http://127.0.0.1:8080/v1#x',
    'http://127.0.0.1:0/v1', 'http://127.0.0.1:99999/v1', 'http://127.0.0.1:08080/v1',
    'http://[::1]:8080/v1', 'http://127.0.0.1:8080/redirect/../v1',
])
def test_only_exact_ipv4_loopback_tunnel_allowed_without_echoing_input(tmp_path, base):
    with pytest.raises(probe.ProbeError) as error:
        probe.freeze(tmp_path / 'invalid', base_url=base)
    assert base not in str(error.value)
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('changes', [dict(request_seconds=41), dict(total_seconds=121),
                                   dict(request_seconds=0), dict(total_seconds=float('nan')),
                                   dict(request_seconds=True)])
def test_budget_cannot_exceed_hard_caps(tmp_path, changes):
    with pytest.raises(probe.ProbeError):
        probe.freeze(tmp_path / 'invalid', **changes)


@pytest.mark.parametrize('mutation', ['source', 'payload', 'sealed_payload', 'policy'])
def test_frozen_drift_blocks_run_before_http(server, monkeypatch, mutation):
    frozen(server)
    if mutation == 'source':
        current = probe.source_map()
        current['lablens/model.py'] = '0' * 64
        monkeypatch.setattr(probe, 'source_map', lambda: current)
    else:
        path = server['output'] / 'plan.json'
        plan = probe.read_json(path)
        if mutation == 'policy':
            plan['retries'] = 1
        else:
            plan['requests'][1]['payload']['max_tokens'] = 123
        path.write_text(json.dumps(plan))
        if mutation in {'sealed_payload', 'policy'}:
            (server['output'] / 'plan.sha256').write_text(probe.sha(path.read_bytes()))
    with pytest.raises(probe.ProbeError):
        probe.run(server['output'])
    assert not server['calls']
    assert not (server['output'] / 'run').exists()


@pytest.mark.parametrize('change', ['revision', 'whitespace', 'delete', 'alias'])
def test_profile_drift_blocks_before_http_and_never_certifies_server_identity(server, change):
    frozen(server)
    path = server['profile_path']
    if change == 'delete':
        path.unlink()
    elif change == 'whitespace':
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        value = probe.read_json(path)
        value['model_revision' if change == 'revision' else 'served_model_name'] = 'b' * 40
        path.write_text(json.dumps(value))
    with pytest.raises(probe.ProbeError):
        probe.run(server['output'])
    assert not server['calls']
    assert not (server['output'] / 'run').exists()


@pytest.mark.parametrize('nested', [False, True])
def test_json_escaped_secret_is_removed_from_saved_response(server, nested):
    encoded = ''.join('\\u%04x' % ord(char) for char in KEY)
    raw_body = ('{"error":"' + encoded + '"}').encode()
    if nested:
        raw_body = json.dumps({'content': raw_body.decode()}).encode()
    server['behavior'] = lambda payload, index: (401, raw_body)
    frozen(server)
    probe.run(server['output'])
    raw = (server['output'] / 'run/01-chinese/response.sanitized.txt').read_text()
    assert '[REDACTED]' in raw
    assert encoded not in raw
    assert_no_secrets(server['output'])


def test_interruption_seals_unknown_intent_and_blocks_rerun(server, monkeypatch):
    frozen(server)
    def interrupted(*_):
        raise KeyboardInterrupt(KEY)
    monkeypatch.setattr(probe, 'transport', interrupted)
    with pytest.raises(KeyboardInterrupt):
        probe.run(server['output'])
    summary = probe.read_json(server['output'] / 'run/summary.json')
    assert summary['status'] == 'interrupted'
    assert summary['attempts'][0]['outcome_unknown']
    assert summary['aggregate_usage']['total_tokens'] is None
    assert_no_secrets(server['output'])
    verify_manifest(server['output'])
    with pytest.raises(FileExistsError):
        probe.run(server['output'])


def test_run_requires_key_and_forbids_overrides(server, monkeypatch, capsys):
    frozen(server)
    monkeypatch.delenv(probe.KEY_ENV)
    with pytest.raises(probe.ProbeError):
        probe.run(server['output'])
    assert not server['calls']
    assert not (server['output'] / 'run').exists()
    assert probe.main(['run', '--output', str(server['output']), '--model', 'changed']) == 2
    assert 'overrides are forbidden' in capsys.readouterr().err


def test_v4_freeze_requires_explicit_protocol_and_uses_production_decision_schema(server, monkeypatch):
    monkeypatch.delenv(probe.KEY_ENV)
    assert probe.main(['freeze', '--output', str(server['output']), '--base-url', server['base_url'],
                       '--review-protocol', probe.DECISION_REVIEW_PROTOCOL]) == 0
    assert not server['calls']
    plan = probe.verify_plan(server['output'])
    assert plan['version'] == 'private-model-compatibility-v2'
    assert plan['review_protocol'] == probe.DECISION_REVIEW_PROTOCOL
    payload = plan['requests'][2]['payload']
    data = json.loads(payload['messages'][1]['content'])
    assert 'server_review_delta' not in data
    assert data['server_review_decisions']['version'] == probe.DECISION_REVIEW_PROTOCOL
    schema = payload['response_format']['json_schema']['schema']
    assert schema['required'] == ['decisions']
    decisions = schema['properties']['decisions']
    assert decisions['required'] == ['a1', 'a2']
    assert decisions['additionalProperties'] is False
    assert {branch['properties']['action']['enum'][0]
            for branch in decisions['properties']['a1']['anyOf']} == {'retain', 'replace'}
    assert decisions['properties']['a2']['properties']['action']['enum'] == ['replace']
    assert 'lablens/review_decisions.py' in plan['source_files']


def test_explicit_v4_runs_three_loopback_requests_and_keeps_protocol_in_evidence(server):
    frozen(server, review_protocol=probe.DECISION_REVIEW_PROTOCOL)
    summary = probe.run(server['output'])
    assert summary['status'] == 'compatibility_passed'
    assert summary['review_protocol'] == probe.DECISION_REVIEW_PROTOCOL
    assert len(server['calls']) == summary['dispatch_intents'] == 3
    assert server['intent_verified'] == [True, True, True]
    detail = probe.read_json(server['output'] / 'run/03-scoped_review/validation.json')
    assert detail['review_protocol'] == probe.DECISION_REVIEW_PROTOCOL
    assert detail['assessment_count'] == 2
    assert detail['semantic_acceptance'] == 'not_evaluated'
    verify_manifest(server['output'])
    assert_no_secrets(server['output'])


@pytest.mark.parametrize('requested,returned', [
    (probe.DECISION_REVIEW_PROTOCOL, probe.DEFAULT_REVIEW_PROTOCOL),
    (probe.DEFAULT_REVIEW_PROTOCOL, probe.DECISION_REVIEW_PROTOCOL),
])
def test_frozen_protocol_rejects_the_other_response_shape_without_fallback(server, requested, returned):
    def behavior(payload, index):
        body = completion(payload)
        if index == 3:
            body['choices'][0]['message']['content'] = json.dumps(review_content(returned))
        return 200, body
    server['behavior'] = behavior
    frozen(server, review_protocol=requested)
    summary = probe.run(server['output'])
    assert summary['status'] == 'blocked'
    assert summary['attempts'][-1]['status'] == 'structured_contract_failed'
    assert summary['review_protocol'] == requested
    assert len(server['calls']) == 3 and summary['no_automatic_retry']
    assert not (server['output'] / 'run/03-scoped_review/validation.json').exists()


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'other', 'payload_policy'])
def test_review_protocol_drift_is_rejected_before_http_even_with_resealed_plan(server, mutation):
    frozen(server, review_protocol=probe.DECISION_REVIEW_PROTOCOL)
    path = server['output'] / 'plan.json'
    plan = probe.read_json(path)
    if mutation == 'missing':
        del plan['review_protocol']
    elif mutation == 'unknown':
        plan['review_protocol'] = 'scoped-review-decisions-v999'
    elif mutation == 'other':
        plan['review_protocol'] = probe.DEFAULT_REVIEW_PROTOCOL
    else:
        item = plan['requests'][2]
        data = json.loads(item['payload']['messages'][1]['content'])
        data['server_review_decisions']['version'] = probe.DEFAULT_REVIEW_PROTOCOL
        item['payload']['messages'][1]['content'] = json.dumps(data)
        item['payload_sha256'] = probe.sha(probe.canonical(item['payload']))
    path.write_text(json.dumps(plan))
    (server['output'] / 'plan.sha256').write_text(probe.sha(path.read_bytes()))
    with pytest.raises(probe.ProbeError):
        probe.run(server['output'])
    assert not server['calls']
    assert not (server['output'] / 'run').exists()


@pytest.mark.parametrize('protocol', [None, '', 'v4', 'scoped-review-decisions-v999', True, []])
def test_freeze_rejects_unknown_protocol_without_network_or_output(server, protocol):
    with pytest.raises(probe.ProbeError):
        frozen(server, review_protocol=protocol)
    assert not server['calls'] and not server['output'].exists()


def test_run_cannot_override_review_protocol_even_with_the_same_value(server, capsys):
    frozen(server, review_protocol=probe.DECISION_REVIEW_PROTOCOL)
    assert probe.main(['run', '--output', str(server['output']),
                       '--review-protocol', probe.DECISION_REVIEW_PROTOCOL]) == 2
    assert 'overrides are forbidden' in capsys.readouterr().err
    assert not server['calls'] and not (server['output'] / 'run').exists()


def test_protocol_is_explicitly_frozen_not_selected_by_later_worker_environment(server, monkeypatch):
    monkeypatch.setenv('LABLENS_REVIEW_PROTOCOL', probe.DECISION_REVIEW_PROTOCOL)
    frozen(server)
    assert probe.verify_plan(server['output'])['review_protocol'] == probe.DEFAULT_REVIEW_PROTOCOL
    monkeypatch.setenv('LABLENS_REVIEW_PROTOCOL', 'unknown-worker-protocol')
    summary = probe.run(server['output'])
    assert summary['status'] == 'compatibility_passed'
    assert summary['review_protocol'] == probe.DEFAULT_REVIEW_PROTOCOL
    assert len(server['calls']) == 3


def test_freeze_cannot_silently_use_an_unselected_production_protocol(server, monkeypatch):
    original = probe._generation_payload
    def wrong_protocol(question, hits, config, **kwargs):
        return original(question, hits, dict(config, review_protocol=probe.DEFAULT_REVIEW_PROTOCOL), **kwargs)
    monkeypatch.setattr(probe, '_generation_payload', wrong_protocol)
    with pytest.raises(probe.ProbeError, match='differs from the selected protocol'):
        frozen(server, review_protocol=probe.DECISION_REVIEW_PROTOCOL)
    assert not server['calls'] and not server['output'].exists()


@pytest.mark.parametrize('mutation', ['both', 'wrong_version', 'no_policy'])
def test_decoder_rejects_ambiguous_or_invalid_frozen_payload_protocol(mutation):
    payload = probe.payloads('lablens-private', probe.DECISION_REVIEW_PROTOCOL)[2]['payload']
    body = completion(payload)
    data = json.loads(payload['messages'][1]['content'])
    if mutation == 'both':
        data['server_review_delta'] = dict(version=probe.DEFAULT_REVIEW_PROTOCOL)
    elif mutation == 'wrong_version':
        data['server_review_decisions']['version'] = probe.DEFAULT_REVIEW_PROTOCOL
    else:
        del data['server_review_decisions']
    payload['messages'][1]['content'] = json.dumps(data)
    assert probe.validate_response('scoped_review', body, payload) == ('structured_contract_failed', None)


@pytest.mark.parametrize('failure', ['duplicate_id', 'escaped_duplicate_id', 'missing_id', 'nonretainable', 'unknown_evidence', 'nonfinite'])
def test_v4_malformed_decisions_fail_closed_over_real_loopback(server, failure):
    def behavior(payload, index):
        body = completion(payload)
        if index == 3:
            parsed = review_content(probe.DECISION_REVIEW_PROTOCOL)
            if failure == 'missing_id':
                del parsed['decisions']['a2']
            elif failure == 'nonretainable':
                parsed['decisions']['a2'] = {'action': 'retain'}
            elif failure == 'unknown_evidence':
                parsed['decisions']['a2']['evidence_ids'] = ['invented-id']
            elif failure == 'nonfinite':
                parsed['decisions']['a2']['explanation'] = float('nan')
            content = json.dumps(parsed)
            if failure in {'duplicate_id', 'escaped_duplicate_id'}:
                key = 'a1' if failure == 'duplicate_id' else r'\u00611'
                content = content.replace('"a1":', '"' + key + '": {"action": "retain"}, "a1":', 1)
            body['choices'][0]['message']['content'] = content
        return 200, body
    server['behavior'] = behavior
    frozen(server, review_protocol=probe.DECISION_REVIEW_PROTOCOL)
    summary = probe.run(server['output'])
    assert summary['status'] == 'blocked'
    assert summary['attempts'][-1]['status'] == 'structured_contract_failed'
    assert len(server['calls']) == 3
    assert summary['attempts'][-1]['usage']['status'] == 'provider_reported'
    verify_manifest(server['output'])


@pytest.mark.parametrize('protocol', probe.REVIEW_PROTOCOLS)
def test_scoped_probe_accepts_legal_historical_insufficiency_without_network(protocol, monkeypatch):
    def forbid_transport(*args, **kwargs):
        raise AssertionError('Pure response validation must not send a request')
    monkeypatch.setattr(probe, 'transport', forbid_transport)
    payload = probe.payloads('lablens-private', protocol)[2]['payload']
    status, detail = probe.validate_response('scoped_review', completion(payload), payload)
    assert status == 'passed'
    assert detail['semantic_acceptance'] == 'not_evaluated'
    assert set(detail['proposition_scope']) == {'review', 'rendered'}
    for audit in detail['proposition_scope'].values():
        assert audit['findings'] == []
        assert audit['rows'][1]['statement_scope'] == 'historical_execution'
        assert audit['rows'][1]['basis'] == 'historical_observation'
        assert audit['semantic_correctness_verified'] is False


@pytest.mark.parametrize('protocol', probe.REVIEW_PROTOCOLS)
def test_scoped_probe_rejects_wrong_runtime_basis_before_any_guard_rewrite(protocol, monkeypatch):
    payload = probe.payloads('lablens-private', protocol)[2]['payload']
    body = completion(payload)
    content = json.loads(body['choices'][0]['message']['content'])
    row = (content['decisions']['a2'] if protocol == probe.DECISION_REVIEW_PROTOCOL
           else content['assessments'][0])
    row['basis'] = 'current_configuration'
    body['choices'][0]['message']['content'] = json.dumps(content)
    def forbid_later_stage(*args, **kwargs):
        raise AssertionError('A raw scope mismatch must stop before later guards or transport')
    monkeypatch.setattr(probe, 'enforce_review_checks', forbid_later_stage)
    monkeypatch.setattr(probe, 'transport', forbid_later_stage)
    assert probe.validate_response('scoped_review', body, payload) == ('structured_contract_failed', None)


@pytest.mark.parametrize('protocol', probe.REVIEW_PROTOCOLS)
def test_scoped_probe_rechecks_exact_rendered_rows_without_network(protocol, monkeypatch):
    payload = probe.payloads('lablens-private', protocol)[2]['payload']
    original_validate = probe.validate_assessments
    calls = []
    def drifted_rendered_report(*args, **kwargs):
        report = original_validate(*args, **kwargs)
        report['assessments'][1]['basis'] = 'current_configuration'
        calls.append('rendered')
        return report
    def forbid_transport(*args, **kwargs):
        raise AssertionError('Pure response validation must not send a request')
    monkeypatch.setattr(probe, 'validate_assessments', drifted_rendered_report)
    monkeypatch.setattr(probe, 'transport', forbid_transport)
    assert probe.validate_response('scoped_review', completion(payload), payload) == ('structured_contract_failed', None)
    assert calls == ['rendered']
