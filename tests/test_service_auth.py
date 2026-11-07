"""Worker service identity tests; every provider call is replaced or forbidden."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.cancellation import CancellationRegistry
from lablens.model import HTTPModel
from lablens.rag import RAGRequest, answer_rag
from lablens.rag_api import router as rag_router
from lablens.service_auth import HEADER, ServiceAuthConfigError, ServiceAuthMiddleware, service_headers
from lablens.workspace_agent import WorkspaceAgent, WorkspaceAgentRequest

KEY = 'round16-worker-test-key-00000000000000000001'


@pytest.fixture
def authenticated(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', KEY)


API_ROUTES = sorted({(method, route.path.replace('{task_id}', '00000000-0000-4000-8000-000000000001'))
              for route in [*app.routes, *rag_router.routes]
              if isinstance(route, APIRoute) and route.path.startswith('/api/')
              for method in route.methods})


@pytest.mark.parametrize('method,path', API_ROUTES)
@pytest.mark.parametrize('credentials', ['missing', 'wrong', 'duplicate'])
def test_every_api_route_rejects_before_payload_parsing(authenticated, method, path, credentials):
    headers = {'Content-Type': 'application/json'}
    if credentials == 'wrong':
        headers[HEADER] = 'wrong-private-value'
    elif credentials == 'duplicate':
        headers = [('Content-Type', 'application/json'), (HEADER, KEY), (HEADER.lower(), KEY)]
    response = TestClient(app).request(method, path, content=b'{not-valid-json', headers=headers)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'
    assert KEY not in response.text and 'wrong-private-value' not in response.text


def test_guard_does_not_read_unauthorized_request_body(authenticated):
    async def forbidden(*_):
        raise AssertionError('Unauthorized request reached the handler or body reader')

    emitted = []

    async def send(message):
        emitted.append(message)

    scope = dict(type='http', path='/api/rag/answer', method='POST', headers=[], client=('127.0.0.1', 1))
    asyncio.run(ServiceAuthMiddleware(forbidden)(scope, forbidden, send))
    assert emitted[0]['status'] == 401


def test_default_is_enabled_and_missing_key_fails_at_startup_and_request(monkeypatch):
    monkeypatch.delenv('LABLENS_AUTH_ENABLED', raising=False)
    monkeypatch.delenv('LABLENS_SERVICE_KEY', raising=False)
    with pytest.raises(ServiceAuthConfigError, match='LABLENS_SERVICE_KEY'):
        with TestClient(app):
            pass
    assert TestClient(app).get('/api/rag/status').status_code == 503


@pytest.mark.parametrize('key', ['', 'short', 'x' * 513, 'x' * 31 + ' ', 'x' * 31 + ',', 'x' * 31 + '\n', 'x' * 31 + '字'])
def test_invalid_config_is_bounded_and_never_echoes_secret(monkeypatch, key):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', key)
    with pytest.raises(ServiceAuthConfigError):
        with TestClient(app):
            pass
    response = TestClient(app).get('/api/dataset')
    assert response.status_code == 503
    assert response.json() == {'detail': 'Worker service authentication is not configured.'}


@pytest.mark.parametrize('value', ['', '0', 'yes', 'true ', 'disabled'])
def test_invalid_auth_flag_cannot_silently_disable(monkeypatch, value):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', value)
    with pytest.raises(ServiceAuthConfigError):
        service_headers()
    assert TestClient(app).get('/api/dataset').status_code == 503


def test_health_remains_public_without_key(authenticated):
    assert TestClient(app).get('/health').json() == {'status': 'ok'}


def test_valid_service_key_accepts_legacy_and_new_routes(authenticated):
    with TestClient(app, headers={HEADER: KEY}) as client:
        assert client.get('/api/dataset').status_code == 200
        assert client.get('/api/rag/status').status_code == 200
        response = client.post('/api/rag/answer', json=dict(mode='extractive', strategy='bm25',
            question='accuracy', snapshot_id='auth:1', documents=[dict(id='doc', title='Result', source='test', text='accuracy 86 percent')]))
        assert response.status_code == 200
        assert response.json()['retrieval']['snapshot_id'] == 'auth:1'


def test_unauthorized_cancel_cannot_create_tombstone(authenticated, monkeypatch):
    import lablens.rag_api as routes
    registry = CancellationRegistry()
    monkeypatch.setattr(routes, 'registry', registry)
    task_id = str(uuid4())
    client = TestClient(app)
    assert client.post(f'/api/rag/executions/{task_id}/cancel').status_code == 401
    with registry.execution(task_id) as active:
        assert not active.cancelled.is_set()
        response = client.post(f'/api/rag/executions/{task_id}/cancel', headers={HEADER: KEY})
        assert response.status_code == 200 and active.cancelled.is_set()


@pytest.mark.parametrize('client_address', [('203.0.113.7', 1), ('192.168.1.4', 1), ('testclient', 1), ('::', 1)])
def test_explicit_dev_mode_rejects_nonloopback_even_with_forwarded_header(monkeypatch, client_address):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'false')
    response = TestClient(app, client=client_address).get('/api/dataset', headers={'X-Forwarded-For': '127.0.0.1'})
    assert response.status_code == 401


@pytest.mark.parametrize('client_address', [('127.0.0.1', 1), ('::1', 1)])
def test_explicit_dev_mode_allows_only_direct_loopback(monkeypatch, client_address):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'false')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', KEY)
    assert TestClient(app, client=client_address).get('/api/dataset').status_code == 200
    assert service_headers() == {}  # Even a configured key is omitted in dev mode.


class SearchModel:
    count = 0

    def complete(self, messages, tools):
        self.count += 1
        assert KEY not in json.dumps([messages, tools])
        return dict(role='assistant', content=None, tool_calls=[dict(id='search-1', type='function',
            function=dict(name='search_workspace', arguments=json.dumps(dict(query='accuracy', strategy='bm25', top_k=3))))]), {}


def agent_request():
    return WorkspaceAgentRequest(question='What was the accuracy?', snapshot_id='auth:1', mode='agent',
        documents=[dict(id='doc', title='Result', source='test', text='The accuracy was 86 percent.')])


def test_agent_forwards_identity_only_to_local_tool_and_not_trace(authenticated, monkeypatch):
    seen = []
    monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', 'http://127.0.0.1:8765')

    def transport(request):
        seen.append(request)
        assert request.headers[HEADER] == KEY
        assert KEY.encode() not in request.content
        return httpx.Response(200, json=answer_rag(RAGRequest.model_validate_json(request.content)))

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = WorkspaceAgent(SearchModel(), client=client, max_steps=1).run(agent_request())
    assert len(seen) == 1 and str(seen[0].url) == 'http://127.0.0.1:8765/api/rag/answer'
    assert result['agent']['tool_calls'] == 1
    assert len(result['retrieval']['searches'][0]['chunk_ids']) == 1
    assert KEY not in json.dumps(result)


def test_agent_never_follows_redirect_with_service_key(authenticated, monkeypatch):
    seen = []
    monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', 'http://127.0.0.1:8765')

    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={'Location': 'https://external.invalid/steal'})

    # Even caller-injected clients configured to follow redirects are bounded.
    with httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
        result = WorkspaceAgent(SearchModel(), client=client).run(agent_request())
    assert seen == ['http://127.0.0.1:8765/api/rag/answer']
    assert result['status'] == 'agent_tool_error'
    assert KEY not in json.dumps(result)


def test_bad_worker_configuration_stops_before_model_call(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.delenv('LABLENS_SERVICE_KEY', raising=False)
    model = SearchModel()
    result = WorkspaceAgent(model).run(agent_request())
    assert model.count == 0 and result['status'] == 'unavailable'


def test_service_identity_is_not_added_to_external_model_requests(authenticated, monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://provider.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'provider-credential-fixture')
    seen = []

    def post(url, **kwargs):
        seen.append((url, kwargs))
        assert HEADER not in kwargs['headers']
        assert KEY not in json.dumps(kwargs)
        return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [
            {'message': {'role': 'assistant', 'content': 'test'}, 'finish_reason': 'stop'}]})

    monkeypatch.setattr('lablens.model.bounded_post', post)
    HTTPModel().complete([{'role': 'user', 'content': 'test'}], [])
    assert len(seen) == 1
    assert seen[0][1]['headers'] == {'Authorization': 'Bearer provider-credential-fixture'}
