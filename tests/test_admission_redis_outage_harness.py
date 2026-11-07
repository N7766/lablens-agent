"""All fault actions are fakes: these tests never start, stop or contact Redis."""
import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('redis_outage', ROOT/'scripts/evaluate_admission_redis_outage.py')
outage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outage)


class FakeRedis:
    def __init__(self, *, stop_failure=False, restore_failure=False):
        self.up = True
        self.stops = self.restores = self.validations = 0
        self.stop_failure, self.restore_failure = stop_failure, restore_failure

    def validate(self):
        self.validations += 1
        return {'pid': 12345, 'port': 6383}

    def stop(self):
        self.stops += 1
        self.up = False
        if self.stop_failure:
            raise RuntimeError('Injected partial stop failure')

    def restore(self):
        self.restores += 1
        if self.restore_failure:
            raise RuntimeError('Injected restoration failure')
        self.up = True
        return {'pid': 23456, 'port': 6383}


def fixture(tmp_path, redis, *, reject_status=503, transport_failure=False, controlled=True):
    workspace, original = str(uuid4()), str(uuid4())
    body = dict(question='Find admission_sentinel evidence for original', strategy='bm25', mode='extractive', top_k=5)
    source = tmp_path/'source.jsonl'
    source.write_text(json.dumps(dict(method='POST', url=f'http://127.0.0.1:8086/api/workspaces/{workspace}/tasks',
        http_status=202, body={'id': original}, request_body=body, idempotency_key='original-key'))+'\n')
    args = argparse.Namespace(endpoint='http://127.0.0.1:8086', worker_endpoint='http://127.0.0.1:8770',
        observations=source, output=tmp_path/'evidence', redis_config=tmp_path/'redis.conf',
        redis_pid_file=tmp_path/'redis.pid', redis_binary=tmp_path/'redis-server',
        task_timeout=.2, stop_timeout=.2, http_timeout=.2)
    usage = dict(model_calls=0, embedding_requests=0, embedded_inputs=0, provider_usage_unknown=False)
    tasks = {original: dict(id=original, status='SUCCEEDED', cache_hit=False, execution_usage=usage)}
    posts = []

    def handle(request):
        path = request.url.path
        if path == '/api/system/status':
            return httpx.Response(200, json=dict(database='available', task_counts={'SUCCEEDED': len(tasks)},
                redis=dict(available=redis.up, rate_limit_enabled=True)))
        if path == '/control/status':
            return httpx.Response(200, json=dict(fixture='synthetic-bm25-extractive-only' if controlled else 'production',
                worker_sha256=outage.sha(ROOT/'scripts/controlled_admission_worker.py'),
                provider_calls=0, active=0, failed=0, started=len(tasks), completed=len(tasks)))
        if path.endswith('/documents'):
            return httpx.Response(200, json=dict(revision=7, documents=[{'id': 'evidence'}]))
        if request.method == 'GET' and '/tasks/' in path:
            return httpx.Response(200, json=tasks[path.rsplit('/', 1)[1]])
        if request.method == 'POST' and path.endswith('/tasks'):
            value = json.loads(request.content)
            posts.append(dict(body=value, key=request.headers['Idempotency-Key']))
            if value['mode'] == 'agent':
                assert value['approved_revision'] == 7
                assert 'requiresApproval' not in value
                if transport_failure:
                    raise httpx.ReadTimeout('secret message must not be copied')
                return httpx.Response(reject_status, json=dict(error='admission_unavailable', retryable=True,
                    retry_after_seconds=1), headers={'Retry-After': '1'})
            if request.headers['Idempotency-Key'] == 'original-key':
                assert value == body
                return httpx.Response(200, json=dict(tasks[original], reused=True))
            task_id = str(uuid4())
            tasks[task_id] = dict(id=task_id, status='SUCCEEDED', cache_hit=False, execution_usage=usage)
            return httpx.Response(202, json=dict(tasks[task_id], reused=False))
        raise AssertionError(path)

    return args, httpx.Client(transport=httpx.MockTransport(handle)), posts, tasks


def read_summary(args):
    return json.loads((args.output/'summary.json').read_text())


def test_full_fake_outage_replays_exact_request_restores_and_preserves_http(tmp_path):
    redis = FakeRedis()
    args, client, posts, tasks = fixture(tmp_path, redis)
    with client:
        assert outage.run(args, client=client, redis=redis) == 0
    assert redis.up and (redis.validations, redis.stops, redis.restores) == (1, 1, 1)
    assert len(posts) == 4 and len(tasks) == 3
    assert len({post['key'] for post in posts}) == 4
    summary = read_summary(args)
    assert summary['completed'] and summary['passed'] and summary['restoration_verified']
    assert all(check['passed'] for check in summary['checks'])
    observations = [json.loads(line) for line in (args.output/'observations.jsonl').read_text().splitlines()]
    assert sum(row['method'] == 'POST' for row in observations) == 4
    assert [row['http_status'] for row in observations if row['method'] == 'POST'] == [503, 200, 202, 202]
    seal = json.loads((args.output/'raw-seal.json').read_text())
    assert all(outage.sha(args.output/name) == digest for name, digest in seal['files'].items())
    with pytest.raises(ValueError, match='cannot be overwritten'):
        outage.run(args, client=client, redis=redis)


@pytest.mark.parametrize('failure', ['unexpected_http', 'transport', 'partial_stop'])
def test_every_outage_failure_restores_without_retrying_a_post(tmp_path, failure):
    redis = FakeRedis(stop_failure=failure == 'partial_stop')
    args, client, posts, tasks = fixture(tmp_path, redis, reject_status=500 if failure == 'unexpected_http' else 503,
                                      transport_failure=failure == 'transport')
    with client:
        assert outage.run(args, client=client, redis=redis) == 2
    assert redis.up and redis.restores == 1
    assert len(posts) == (0 if failure == 'partial_stop' else 1)
    assert len(tasks) == 1
    summary = read_summary(args)
    assert not summary['passed'] and summary['restoration_verified'] and 'outage_failure' in summary
    assert 'secret message' not in ''.join(p.read_text() for p in args.output.iterdir())


def test_failed_restoration_is_retained_separately_from_initial_failure(tmp_path):
    redis = FakeRedis(restore_failure=True)
    args, client, posts, _ = fixture(tmp_path, redis, reject_status=500)
    with client:
        assert outage.run(args, client=client, redis=redis) == 2
    summary = read_summary(args)
    assert not redis.up and not summary['restoration_verified']
    assert summary['outage_failure']['error_type'] == 'AssertionError'
    assert summary['restoration_failure']['error_type'] == 'RuntimeError'
    assert len(posts) == 1


def test_interrupt_after_stop_also_restores_before_returning_130(tmp_path):
    redis = FakeRedis()
    def interrupted_stop():
        redis.stops += 1
        redis.up = False
        raise KeyboardInterrupt()
    redis.stop = interrupted_stop
    args, client, posts, _ = fixture(tmp_path, redis)
    with client:
        assert outage.run(args, client=client, redis=redis) == 130
    assert redis.up and redis.restores == 1 and posts == []
    summary = read_summary(args)
    assert summary['interrupted'] and summary['restoration_verified']


def test_uncontrolled_worker_fails_preflight_before_any_signal_or_post(tmp_path):
    redis = FakeRedis()
    args, client, posts, _ = fixture(tmp_path, redis, controlled=False)
    with client:
        assert outage.run(args, client=client, redis=redis) == 2
    assert redis.up and redis.stops == redis.restores == redis.validations == 0
    assert posts == []


@pytest.mark.parametrize('value', ['https://example.com', 'http://127.0.0.1@remote.invalid:8086',
                                  'http://localhost:8086/path', 'http://localhost:8086?q=secret', 'http://localhost'])
def test_only_explicit_loopback_origins_are_allowed(value):
    with pytest.raises(ValueError, match='loopback'):
        outage.origin(value)


def config_files(tmp_path, text):
    binary = tmp_path/'redis-server'
    binary.write_text('not an executable program; never launched in tests')
    binary.chmod(0o700)
    pid_file = tmp_path/'redis.pid'
    pid_file.write_text('12345')
    config = tmp_path/'redis.conf'
    config.write_text(f'bind 127.0.0.1\npidfile {pid_file}\n{text}\n')
    return config, pid_file, binary


@pytest.mark.parametrize('setting', ['port 6380', 'port 6383\nrequirepass secret',
                                    'port 6383\ninclude other.conf', 'port 6383\nbind 0.0.0.0'])
def test_foreign_port_auth_include_and_nonloopback_config_are_rejected(tmp_path, setting):
    files = config_files(tmp_path, setting)
    with pytest.raises(ValueError):
        outage.validate_config(*files)


@pytest.mark.parametrize('changed', [{'process_id': '99999'}, {'tcp_port': '6380'}, {'config_file': '/unrelated/redis.conf'}])
def test_live_identity_mismatch_prevents_any_signal(tmp_path, monkeypatch, changed):
    config, pid_file, binary = config_files(tmp_path, 'port 6383')
    redis = outage.RedisFixture(config, pid_file, binary, lambda *args, **kwargs: None)
    monkeypatch.setattr(outage.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='redis-server 127.0.0.1:6383'))
    identity = dict(process_id='12345', tcp_port='6383', config_file=str(config)) | changed
    monkeypatch.setattr(redis, 'info', lambda: identity)
    signals = []
    monkeypatch.setattr(outage.os, 'kill', lambda *args: signals.append(args))
    with pytest.raises(ValueError, match='identity'):
        redis.validate()
    assert signals == []


def test_matching_live_identity_accepts_standard_redis_rewritten_process_title(tmp_path, monkeypatch):
    config, pid_file, binary = config_files(tmp_path, 'port 6383')
    redis = outage.RedisFixture(config, pid_file, binary, lambda *args, **kwargs: None)
    monkeypatch.setattr(outage.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='redis-server 127.0.0.1:6383'))
    monkeypatch.setattr(redis, 'info', lambda: dict(process_id='12345', tcp_port='6383', config_file=str(config), redis_version='fixture'))
    result = redis.validate()
    assert result['pid'] == 12345 and result['configuration_in_ps'] is False


def test_wrong_source_origin_cannot_be_used_as_replay(tmp_path):
    redis = FakeRedis()
    args, client, _, _ = fixture(tmp_path, redis)
    client.close()
    with pytest.raises(ValueError, match='exact target'):
        outage.replay_input(args.observations, 'http://127.0.0.1:8081')
