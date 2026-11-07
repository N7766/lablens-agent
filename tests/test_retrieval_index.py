"""Persistent-index engineering tests; mocked vectors are not model-quality evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
from threading import Event
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from lablens import rag
from lablens import retrieval_index as ri
from lablens.cancellation import registry
from lablens.rag_api import router


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(rag, 'load_dotenv', lambda: None)
    for name in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY', 'EMBEDDING_BASE_URL',
                 'EMBEDDING_MODEL', 'EMBEDDING_API_KEY', 'EMBEDDING_MODEL_REVISION',
                 'RAG_TIMEOUT_SECONDS', 'RAG_TOTAL_SECONDS', 'EMBEDDING_BATCH_SIZE',
                 'LABLENS_INDEX_MAX_ENTRIES'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LABLENS_INDEX_DB', str(tmp_path / 'derived.sqlite3'))
    rag.clear_embedding_cache()


def snapshot(**updates):
    value = dict(snapshot_id='workspace:revision:1', documents=[
        dict(id='mysql', title='Transactions', text='MySQL transaction isolation preserves snapshots.', source='mysql.md'),
        dict(id='redis', title='Cache', text='Redis stores cached values.', source='redis.md')])
    value.update(updates)
    return ri.IndexRequest(**value)


def query(index=None, **updates):
    value = (index or snapshot()).model_dump()
    value.update(question='MySQL transaction', top_k=2)
    value.update(updates)
    return rag.RAGRequest(**value)


def provider(monkeypatch, post=None):
    monkeypatch.setenv('EMBEDDING_BASE_URL', 'https://embedding.test/v1')
    monkeypatch.setenv('EMBEDDING_MODEL', 'mock-model')
    monkeypatch.setenv('EMBEDDING_API_KEY', 'not-a-real-secret')
    calls = []
    def mock(url, **kwargs):
        inputs = kwargs['json']['input']
        calls.append(inputs)
        if post:
            return post(url, **kwargs)
        vectors = [[1., 0.] if 'mysql' in text.lower() else [0., 1.] for text in inputs]
        return httpx.Response(200, json={'data': [{'index': i, 'embedding': v} for i, v in enumerate(vectors)],
                                        'usage': {'total_tokens': 7 * len(inputs)}},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr('lablens.rag.bounded_post', mock)
    return calls


def sql(command, args=()):
    with sqlite3.connect(os.environ['LABLENS_INDEX_DB']) as db:
        result = db.execute(command, args).fetchall()
    return result


def test_disabled_store_preserves_request_local_behavior(monkeypatch):
    monkeypatch.delenv('LABLENS_INDEX_DB')
    assert ri.build_index(snapshot())['status'] == 'disabled'
    assert ri.index_status(snapshot())['status'] == 'disabled'
    result = rag.answer_rag(query())
    assert result['status'] == 'excerpts'
    assert result['retrieval']['index_reused'] is False
    assert result['retrieval']['index_fallback_reason'] == 'disabled'


def test_bm25_build_status_idempotency_and_exact_retrieval(monkeypatch):
    monkeypatch.setattr('lablens.rag.bounded_post', lambda *a, **kw: pytest.fail('Offline build cannot call a provider'))
    reference = rag.answer_rag(query())
    first = ri.build_index(snapshot())
    assert first['status'] == 'ready' and first['chunk_count'] == 2
    assert first['index_reused'] is False
    second = ri.build_index(snapshot())
    assert second['index_id'] == first['index_id'] and second['index_reused'] is True
    assert second['usage']['embedding_requests'] == 0
    monkeypatch.setattr(rag, 'chunk_documents', lambda documents: pytest.fail('READY hit must reuse the chunk manifest'))
    result = rag.answer_rag(query())
    assert result['retrieval']['index_id'] == first['index_id']
    assert result['retrieval']['index_reused'] is True
    assert result['retrieval']['hits'] == reference['retrieval']['hits']
    assert ri.index_status(snapshot())['status'] == 'ready'


def test_ready_survives_real_fresh_process():
    first = ri.build_index(snapshot())
    script = """import json,sys
from lablens.retrieval_index import IndexRequest,index_status
print(json.dumps(index_status(IndexRequest.model_validate_json(sys.stdin.read()))))
"""
    completed = subprocess.run([sys.executable, '-c', script], input=snapshot().model_dump_json(),
                               capture_output=True, text=True, check=True, timeout=15)
    result = json.loads(completed.stdout)
    assert result['index_id'] == first['index_id'] and result['status'] == 'ready'
    assert result['usage']['embedding_requests'] == 0


@pytest.mark.parametrize('change', ['edit', 'delete', 'revision', 'order', 'source'])
def test_immutable_identity_invalidates_all_snapshot_changes(change):
    old = snapshot()
    ready = ri.build_index(old)
    value = old.model_dump()
    if change == 'edit': value['documents'][0]['text'] = 'Changed transaction behavior.'
    if change == 'delete': value['documents'].pop(0)
    if change == 'revision': value['snapshot_id'] = 'workspace:revision:2'
    if change == 'order': value['documents'].reverse()
    if change == 'source': value['documents'][0]['source'] = 'another-source.md'
    changed = ri.IndexRequest(**value)
    assert ri.index_status(changed)['status'] == 'missing'
    retrieved = rag.answer_rag(query(changed))
    assert retrieved['retrieval']['index_reused'] is False
    assert retrieved['retrieval']['index_id'] is None
    new = ri.build_index(changed)
    assert new['index_id'] != ready['index_id']
    assert ri.index_status(old)['status'] == 'ready'  # Historical snapshots are independently addressable.


def test_dense_build_skips_query_and_reuses_vectors_after_cache_clear(monkeypatch):
    calls = provider(monkeypatch)
    request = snapshot(strategy='dense')
    built = ri.build_index(request)
    assert built['status'] == 'ready'
    assert len(calls) == built['usage']['embedding_requests'] == 1
    assert built['usage']['embedded_inputs'] == 2
    assert built['usage']['embedding_tokens'] == 14
    rag.clear_embedding_cache()
    result = rag.answer_rag(query(request, strategy='hybrid'))
    assert result['retrieval']['index_reused'] is True
    assert result['retrieval']['index_id'] == built['index_id']
    assert result['usage']['embedding_requests'] == 1
    assert result['usage']['embedded_inputs'] == 1
    assert result['usage']['persistent_vector_hits'] == 2
    assert len(calls) == 2
    assert ri.build_index(request)['usage']['embedding_requests'] == 0
    assert len(calls) == 2
    serialized = json.dumps(built)
    assert 'not-a-real-secret' not in serialized
    assert rag._sha('not-a-real-secret') not in serialized


@pytest.mark.parametrize('name,value', [('EMBEDDING_MODEL_REVISION', 'new-revision'),
                                        ('EMBEDDING_MODEL', 'new-model'),
                                        ('EMBEDDING_API_KEY', 'changed-credential'),
                                        ('EMBEDDING_BASE_URL', 'https://another.test/v1')])
def test_semantic_identity_covers_provider_model_revision_and_credential(monkeypatch, name, value):
    provider(monkeypatch)
    request = snapshot(strategy='dense')
    first = ri.build_index(request)
    monkeypatch.setenv(name, value)
    assert ri.index_status(request)['status'] == 'missing'
    second = ri.build_index(request)
    assert first['index_id'] != second['index_id']
    if name == 'EMBEDDING_API_KEY':
        assert first['config_id'] == second['config_id']  # Public ID must not disclose credential fingerprint.
    else:
        assert first['config_id'] != second['config_id']


def test_concurrent_duplicate_build_has_one_provider_owner(monkeypatch):
    entered, release = Event(), Event()
    def delayed(url, **kwargs):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1., 0.]}
                                                for i in range(len(kwargs['json']['input']))],
                                        'usage': {'total_tokens': 14}}, request=httpx.Request('POST', url))
    calls = provider(monkeypatch, delayed)
    request = snapshot(strategy='dense')
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(ri.build_index, request)
        assert entered.wait(5)
        second = ri.build_index(request)
        assert second['status'] == 'building' and second['usage']['embedding_requests'] == 0
        release.set()
        first = future.result(5)
    assert first['status'] == 'ready'
    assert len(calls) == 1


def test_provider_timeout_is_terminal_and_unknown_usage_is_preserved(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('mock timeout')
    calls = provider(monkeypatch, timeout)
    request = snapshot(strategy='dense')
    first = ri.build_index(request)
    assert first['status'] == 'failed'
    assert first['usage']['embedding_requests'] == 1
    assert first['usage']['provider_usage_unknown'] is True
    assert first['usage']['embedding_tokens'] is None
    second = ri.build_index(request)
    assert second['status'] == 'failed' and second['usage']['embedding_requests'] == 0
    assert second['build_usage'] == first['usage']
    assert len(calls) == 1
    result = rag.answer_rag(query(request))
    assert result['status'] == 'unavailable'
    assert result['usage']['embedding_requests'] == 0 and len(calls) == 1
    assert result['retrieval']['index_fallback_reason'] == 'failed'


def test_cancel_after_provider_response_does_not_publish_or_retry(monkeypatch):
    task_id = str(uuid4())
    def cancelled(url, **kwargs):
        registry.cancel(task_id)
        return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1., 0.]}
                                                for i in range(len(kwargs['json']['input']))],
                                        'usage': {'total_tokens': 14}}, request=httpx.Request('POST', url))
    calls = provider(monkeypatch, cancelled)
    request = snapshot(strategy='dense')
    with registry.execution(task_id):
        result = ri.build_index(request)
    assert result['status'] == 'cancelled'
    assert result['usage']['embedding_tokens'] == 14
    assert ri.index_status(request)['status'] == 'cancelled'
    assert ri.build_index(request)['status'] == 'cancelled'
    assert len(calls) == 1


def test_expired_build_remains_terminal_unknown_and_not_ready(monkeypatch):
    provider(monkeypatch)
    request = snapshot(strategy='dense')
    store, identity, _ = ri._context(request)
    row, owned = store.claim(identity, 1)
    assert owned
    sql('UPDATE retrieval_indexes SET lease_until=?', (time.time() - 1,))
    result = ri.index_status(request)
    assert result['status'] == 'failed'
    assert result['build_usage']['provider_usage_unknown'] is True
    assert result['build_usage']['embedding_requests'] is None
    assert ri.build_index(request)['status'] == 'failed'
    with pytest.raises(rag.RAGError, match='lost its lease'):
        store.finish(row, 'ready', rag._new_usage(), chunks=[], vectors=[])


@pytest.mark.parametrize('damage', ['checksum', 'nan_vector', 'dimension', 'source_quote'])
def test_corrupted_artifact_is_quarantined_without_retry(monkeypatch, damage):
    calls = provider(monkeypatch)
    request = snapshot(strategy='dense')
    ri.build_index(request)
    raw = sql('SELECT payload FROM retrieval_indexes')[0][0]
    payload = json.loads(raw)
    if damage == 'checksum':
        updated = raw.replace('Transactions', 'WRONG')
        sql('UPDATE retrieval_indexes SET payload=?', (updated,))
    else:
        if damage == 'nan_vector': payload['vectors'][0] = [float('nan'), 0.]
        if damage == 'dimension': payload['vectors'][0] = [1., 0., 0.]
        if damage == 'source_quote': payload['chunks'][0]['text'] = 'Invented source excerpt'
        updated = json.dumps(payload)
        sql('UPDATE retrieval_indexes SET payload=?,payload_sha256=?', (updated, rag._sha(updated)))
    status = ri.index_status(request)
    assert status['status'] == 'failed'
    assert ri.build_index(request)['status'] == 'failed'
    assert rag.answer_rag(query(request))['status'] == 'unavailable'
    assert len(calls) == 1


def test_bounded_capacity_does_not_evict_terminal_or_ready_identities(monkeypatch):
    monkeypatch.setenv('LABLENS_INDEX_MAX_ENTRIES', '1')
    first = ri.build_index(snapshot())
    second = ri.build_index(snapshot(snapshot_id='other:revision:1'))
    assert second['status'] == 'capacity_exceeded'
    assert ri.index_status(snapshot())['index_id'] == first['index_id']
    assert sql('SELECT COUNT(*) FROM retrieval_indexes')[0][0] == 1


def test_artifact_size_limit_does_not_publish(monkeypatch):
    monkeypatch.setattr(ri, 'MAX_ARTIFACT_BYTES', 10)
    result = ri.build_index(snapshot())
    assert result['status'] == 'failed'
    assert ri.index_status(snapshot())['status'] == 'failed'
    assert sql('SELECT payload FROM retrieval_indexes')[0][0] is None


def test_storage_failure_is_explicit_and_bm25_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv('LABLENS_INDEX_DB', str(tmp_path))  # Existing directory is not a database.
    assert ri.build_index(snapshot())['status'] == 'unavailable'
    assert ri.index_status(snapshot())['status'] == 'unavailable'
    result = rag.answer_rag(query())
    assert result['status'] == 'excerpts'
    assert result['retrieval']['index_fallback_reason'] == 'storage_unavailable'


def test_http_contract_strict_and_build_uses_task_cancellation(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = snapshot().model_dump()
    assert client.post('/api/rag/indexes/status', json=payload).json()['status'] == 'missing'
    built = client.post('/api/rag/indexes/build', json=payload)
    assert built.status_code == 200 and built.json()['status'] == 'ready'
    assert client.post('/api/rag/indexes/status', json=payload).json()['index_id'] == built.json()['index_id']
    assert client.post('/api/rag/indexes/build', json={**payload, 'unknown': 1}).status_code == 422
    assert client.post('/api/rag/indexes/build', json={**payload, 'documents': payload['documents'] * 2}).status_code == 422
    task_id = str(uuid4())
    client.post('/api/rag/executions/' + task_id + '/cancel')
    cancelled = client.post('/api/rag/indexes/build', json=payload, headers={'X-Lablens-Task-Id': task_id})
    assert cancelled.status_code == 409


@pytest.mark.parametrize('documents', [[], [dict(id='emptyish', title='Whitespace edges', text='  a  ', source='edges')]])
def test_empty_and_trimmed_chunk_manifests_remain_valid(documents):
    request = snapshot(documents=documents)
    assert ri.build_index(request)['status'] == 'ready'
    assert ri.index_status(request)['status'] == 'ready'


def test_cross_batch_dimension_mismatch_never_publishes(monkeypatch):
    sequence = iter([[1., 0.], [1., 0., 0.]])
    def dimensions(url, **kwargs):
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': next(sequence)}],
                                        'usage': {'total_tokens': 7}}, request=httpx.Request('POST', url))
    calls = provider(monkeypatch, dimensions)
    monkeypatch.setenv('EMBEDDING_BATCH_SIZE', '1')
    result = ri.build_index(snapshot(strategy='dense'))
    assert result['status'] == 'failed'
    assert len(calls) == 2
    assert 'dimensions differ' in result['error']
