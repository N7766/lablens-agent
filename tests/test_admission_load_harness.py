"""Controls for the zero-provider admission harness; all HTTP stays in-process."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loadtest = load('evaluate_admission_load')
worker = load('controlled_admission_worker')


def test_fixed_arrival_schedule_is_not_completion_driven():
    slots = loadtest.planned_slots(60, (2, 20, 2))
    assert len(slots) == 1440
    assert slots[0]['scheduled_offset_s'] == 0
    assert slots[120]['scheduled_offset_s'] == 60
    assert slots[1320]['scheduled_offset_s'] == 120
    assert slots[-1]['scheduled_offset_s'] == 179.5
    assert len({row['sequence'] for row in slots}) == 1440


def test_accounting_does_not_drop_missed_or_failed_arrivals():
    rows = [dict(outcome='accepted', send_offset_s=0, http_status=202, response_ms=10),
            dict(outcome='rejected', send_offset_s=.1, http_status=503, response_ms=2, rejection_code='global_queue_full'),
            dict(outcome='transport_error', send_offset_s=.2, http_status=None, response_ms=5000),
            dict(outcome='unsent', send_offset_s=None, unsent_reason='client_in_flight_limit')]
    actual = loadtest.stage_metrics(rows)
    assert (actual['scheduled'], actual['sent'], actual['responses']) == (4, 3, 2)
    assert actual['accepted'] == actual['rejected'] == actual['transport_errors'] == actual['unsent'] == 1
    assert actual['submission_latency']['n'] == 3  # Includes failed/slow observations.


@pytest.mark.parametrize('endpoint', ['https://example.com', 'http://127.0.0.1@evil.invalid',
                                      'http://127.0.0.1:8085/path', 'http://localhost?q=1'])
def test_harness_cannot_target_remote_services(endpoint):
    with pytest.raises(ValueError, match='loopback'):
        loadtest.loopback_url(endpoint)


def test_worker_uses_real_bm25_and_preserves_snapshot_with_zero_usage(tmp_path, monkeypatch):
    original = worker.rag.provider_config
    monkeypatch.setattr(worker.rag, 'provider_config', original)
    app = worker.create_app(1, tmp_path / 'worker.jsonl')
    try:
        with TestClient(app) as client:
            request = dict(question='Find admission_sentinel evidence', strategy='bm25', mode='extractive',
                           snapshot_id='workspace:7', documents=[dict(id='doc', title='Synthetic sentinel',
                           source='synthetic:test', text='admission_sentinel means SYNTHETIC_VALUE')])
            response = client.post('/api/rag/answer', json=request, headers={'X-LabLens-Task-Id': str(uuid4())})
            assert response.status_code == 200
            result = response.json()
            assert result['status'] == 'excerpts'
            assert result['retrieval']['snapshot_id'] == 'workspace:7'
            assert result['retrieval']['strategy'] == 'bm25'
            assert result['mode'] == 'extractive'
            assert 'SYNTHETIC_VALUE' in result['answer']
            assert result['usage']['model_calls'] == result['usage']['embedding_requests'] == 0
            assert result['usage']['provider_usage_unknown'] is False
            assert client.get('/control/status').json()['completed'] == 1
            for update in [dict(mode='live'), dict(mode='agent'), dict(strategy='dense'), dict(strategy='hybrid')]:
                rejected = client.post('/api/rag/answer', json={**request, **update}, headers={'X-LabLens-Task-Id': str(uuid4())})
                assert rejected.status_code == 422
            assert client.post('/api/rag/answer', json=request).status_code == 422
            assert client.get('/control/status').json()['started'] == 1
    finally:
        app.state.evidence_log.close()
    events = [json.loads(line) for line in (tmp_path / 'worker.jsonl').read_text().splitlines()]
    assert [event['event'] for event in events] == ['started', 'completed']
    with pytest.raises(FileExistsError):
        worker.create_app(1, tmp_path / 'worker.jsonl')


def test_harness_retains_unsent_and_never_retries_slow_or_rejected_posts(tmp_path, monkeypatch):
    submitted, stored = [], {}
    async def handle(request):
        path = request.url.path
        if path == '/api/system/status':
            return httpx.Response(200, json=dict(database='available', task_counts={'SUCCEEDED': len(stored)},
                workers=dict(concurrency=2, max_observed_active=1), admission=dict(global_pending_limit=12, workspace_pending_limit=8)))
        if path == '/control/status':
            return httpx.Response(200, json=dict(fixed_delay_ms=400, started=len(stored), completed=len(stored), failed=0, max_active=1))
        if path == '/api/workspaces':
            return httpx.Response(201, json={'id': str(uuid4())})
        if '/documents/' in path:
            return httpx.Response(200, json={})
        if '/actuator/' in path:
            return httpx.Response(200, json={'measurements': [{'statistic': 'VALUE', 'value': .5}]})
        if path.endswith('/tasks') and request.method == 'POST':
            submitted.append(request.headers['Idempotency-Key'])
            await asyncio.sleep(.03)
            if len(submitted) % 2 == 0:
                return httpx.Response(503, json={'error': 'global_queue_full'}, headers={'Retry-After': '1'})
            task_id = str(uuid4())
            stored[task_id] = dict(id=task_id, status='SUCCEEDED', cache_hit=False, result={'usage': {
                'model_calls': 0, 'embedding_requests': 0, 'embedded_inputs': 0, 'provider_usage_unknown': False}})
            return httpx.Response(202, json={'id': task_id})
        if '/tasks/' in path:
            return httpx.Response(200, json=stored[path.rsplit('/', 1)[1]])
        raise AssertionError(path)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(loadtest.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(loadtest, 'source_hashes', lambda jar: {'tested_jar': 'frozen'})
    args = argparse.Namespace(endpoint='http://127.0.0.1:8085', worker_endpoint='http://127.0.0.1:8769',
        output=tmp_path / 'run', variant='candidate', source_commit='test', jar=tmp_path / 'unused.jar',
        environment_json=None, phase_seconds=.1, under_rate=100, over_rate=100, recovery_rate=100,
        max_requests=100, max_in_flight=1, http_timeout=.2, max_lateness_ms=100,
        sample_interval=.02, metrics_interval=1, drain_timeout=1, worker_delay_ms=400,
        workspaces=4, global_limit=12, workspace_limit=8)
    assert asyncio.run(loadtest.run(args)) == 2  # Unsent is an explicit invalid trial, never a pass.
    summary = json.loads((args.output / 'summary.json').read_text())
    records = [json.loads(line) for line in (args.output / 'requests.jsonl').read_text().splitlines()]
    assert len(records) == 30
    assert len({row['sequence'] for row in records}) == 30
    assert summary['counts']['unsent'] > 0
    assert summary['counts']['rejected'] > 0
    assert summary['max_client_in_flight'] == 1
    assert len(submitted) == len(set(submitted)) == summary['counts']['sent']
    assert summary['counts']['sent'] + summary['counts']['unsent'] == 30
    assert summary['counts']['accepted'] == len(stored)
    with pytest.raises(ValueError, match='cannot be overwritten'):
        asyncio.run(loadtest.run(args))
