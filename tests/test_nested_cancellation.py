"""Real loopback retrieval plus controlled provider responses; zero paid calls."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from lablens import rag, rag_api
from lablens.api import app
from lablens.cancellation import CancellationRegistry
from lablens.workspace_agent import WorkspaceAgent
from test_workspace_agent import request, call


def config(*args, **kwargs):
    return dict(base='https://controlled.invalid', model='nested-cancel-fixture', key='unused',
                revision='test', batch_size=1, timeout=30, deadline=time.monotonic()+60)


def test_generic_live_cancel_after_provider_response_preserves_usage_in_http_and_ack(monkeypatch):
    registry = CancellationRegistry(); task_id = str(uuid4()); calls = []
    monkeypatch.setattr(rag_api, 'registry', registry)
    monkeypatch.setattr(rag, 'provider_config', config)
    def provider(url, **kwargs):
        calls.append(kwargs)
        assert registry.cancel(task_id)['stopped'] is False
        return httpx.Response(200, request=httpx.Request('POST', url), json=dict(
            choices=[dict(message=dict(content='{}'), finish_reason='stop')],
            usage=dict(prompt_tokens=2, completion_tokens=3, total_tokens=5)))
    monkeypatch.setattr(httpx, 'post', provider)
    req = request().model_dump(exclude={'conversation_context'}); req['mode'] = 'live'
    response = TestClient(app).post('/api/rag/answer', json=req, headers={'X-LabLens-Task-Id': task_id})
    assert response.status_code == 409
    result = response.json()['detail']
    assert result['status'] == 'agent_cancelled' and result['answer'] == ''
    assert result['usage']['model_calls'] == len(calls) == 1
    assert result['usage']['generation_tokens']['total_tokens'] == 5
    assert result['usage']['provider_usage_unknown'] is False
    ack = registry.cancel(task_id)
    assert ack['stopped'] is True and ack['usage'] == result['usage']


def test_nested_hybrid_tool_inherits_cancellation_and_does_not_send_second_embedding(monkeypatch):
    registry = CancellationRegistry(); task_id = str(uuid4()); seen, calls = [], []
    monkeypatch.setattr(rag, 'provider_config', config)
    with rag._CACHE_LOCK:
        rag._VECTOR_CACHE.clear()
    def provider(url, **kwargs):
        calls.append(kwargs)
        assert url.endswith('/embeddings')
        assert registry.cancel(task_id)['stopped'] is False
        return httpx.Response(200, request=httpx.Request('POST', url), json=dict(
            data=[dict(index=0, embedding=[1., 0.])], usage=dict(total_tokens=7)))
    monkeypatch.setattr(httpx, 'post', provider)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            parent = self.headers.get('X-LabLens-Parent-Task-Id')
            seen.append(parent)
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with registry.child_execution(parent):
                result = rag.answer_rag(rag.RAGRequest.model_validate(data))
            body = json.dumps({'detail': result}).encode()
            self.send_response(409); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    class Search:
        def __init__(self): self.count = 0
        def complete(self, messages, tools):
            self.count += 1
            assert self.count == 1
            return call(1, 'search_workspace', dict(query='accuracy', strategy='hybrid', top_k=3))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', f'http://127.0.0.1:{server.server_port}')
        model = Search()
        with registry.execution(task_id) as execution:
            result = WorkspaceAgent(model).run(request(strategy='hybrid'))
            execution.result = result
        assert seen == [task_id]
        assert result['status'] == 'agent_cancelled' and result['answer'] == ''
        assert result['usage']['model_calls'] == model.count == 1
        assert result['usage']['embedding_requests'] == len(calls) == 1
        assert result['usage']['embedding_tokens'] == 7
        assert result['usage']['generation_tokens']['total_tokens'] == 5
        assert result['usage']['provider_usage_unknown'] is False
        assert registry.cancel(task_id)['stopped'] is True
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
