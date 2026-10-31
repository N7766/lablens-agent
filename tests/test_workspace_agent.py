"""Controlled model decisions + genuine loopback HTTP tools; not model-quality evidence."""
from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest
from fastapi.testclient import TestClient

from lablens.agent import Agent
from lablens import rag
from lablens.assessment import validate_assessments, evidence_catalog
from lablens.model import ModelError
from lablens.workspace_agent import WorkspaceAgent, WorkspaceAgentRequest, WorkspaceContext


def request(**overrides):
    value = dict(question='What accuracy was observed?', mode='agent', strategy='bm25', top_k=3,
                 snapshot_id='workspace:7', documents=[dict(id='run:1', title='Observed report',
                 text='The measured accuracy was 86 percent. This is a synthetic test fixture.', source='report.md')])
    value.update(overrides)
    return WorkspaceAgentRequest(**value)


def call(step, name, args):
    return {'role': 'assistant', 'tool_calls': [{'id': 'call-'+str(step), 'type': 'function',
             'function': {'name': name, 'arguments': json.dumps(args)}}]}, {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}


def report(hits, verdict='supported'):
    hit = hits[0] if hits else None
    return {'assessments': [dict(statement='The measured accuracy was 86 percent.',
             verdict=verdict if hit else 'insufficient', explanation='The report contains this observation.' if hit else 'No retrieved observation is available.',
             basis='historical_observation', evidence_ids=list(evidence_catalog([hit]))[:1] if hit else [])]}


class DecisionModel:
    """Explicitly scripted test model; production uses HTTPModel tool-call responses."""
    mode = 'scripted_test_model'
    def __init__(self, query='accuracy'):
        self.count = 0
        self.query = query
        self.schemas = []
    def complete(self, messages, tools):
        self.count += 1
        self.schemas = tools
        outputs = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        hits = next((o['hits'] for o in outputs if 'hits' in o), [])
        if self.count == 1:
            return call(self.count, 'search_workspace', dict(query=self.query, strategy='bm25', top_k=3))
        if self.count == 2 and hits:
            return call(self.count, 'verify_sources', {'chunk_ids': [h['chunk_id'] for h in hits]})
        return call(self.count, 'submit_report', report(hits))


@contextmanager
def retrieval_server(transform=None):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(dict(path=self.path, body=data, remaining=self.headers.get('X-LabLens-Budget-Seconds')))
            value = rag.answer_rag(rag.RAGRequest.model_validate(data))
            if transform:
                value = transform(value)
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', seen
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


@pytest.fixture
def controlled_review(monkeypatch):
    seen = []
    monkeypatch.setattr(rag, 'provider_config', lambda *args, **kwargs: {'deadline': time.monotonic()+80})
    def review(question, raw, hits, usage, config, *, observer=None, context_guard=None, context_data=None):
        seen.append(dict(hits=hits, raw=raw, context_data=context_data))
        if context_guard:
            context_guard(rag._generation_payload(question, hits, {'model': 'controlled-test-model'}, draft=raw, context_data=context_data))
        usage['model_calls'] += 1
        for key, value in {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}.items():
            usage['generation_tokens'][key] += value
        return validate_assessments(question, raw, hits)
    monkeypatch.setattr(rag, 'review_assessments', review)
    return seen


def test_existing_agent_branch_uses_model_selected_real_http_tool_and_verifies_source(monkeypatch, controlled_review):
    req = request(); model = DecisionModel()
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = Agent(model).run(req.question, workspace_context=req.model_dump(exclude={'question', 'mode'}))
    assert result['status'] == 'answered'
    assert result['agent']['state'] == 'completed'
    assert len(seen) == 1 and seen[0]['path'] == '/api/rag/answer'
    assert seen[0]['body']['snapshot_id'] == req.snapshot_id
    assert seen[0]['body']['documents'] == [d.model_dump() for d in req.documents]
    assert seen[0]['body']['question'] == 'accuracy'  # The model-selected query, not a fixed orchestration query.
    assert seen[0]['body']['mode'] == 'extractive'
    assert 0 < float(seen[0]['remaining']) <= 75
    assert result['usage']['model_calls'] == 4 and result['usage']['embedding_requests'] == 0
    assert result['usage']['generation_tokens'] == {'prompt_tokens': 7, 'completion_tokens': 10, 'total_tokens': 17}
    assert result['agent']['documents_sha256'] == rag.documents_sha256(req.documents)
    assert result['agent']['verified_chunk_ids'] == [h['chunk_id'] for h in controlled_review[0]['hits']]
    assert [e['name'] for e in result['agent']['trace'] if e['event']=='tool'] == ['search_workspace', 'verify_sources', 'submit_report']


def test_no_retrieval_matches_stops_with_insufficient_report(monkeypatch, controlled_review):
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(DecisionModel('nonmatchingterm')).run(request())
    assert result['status'] == 'refused' and result['agent']['state'] == 'insufficient_evidence'
    assert result['citations'] == [] and result['evidence_verdict'] == 'insufficient'
    assert len(seen) == 1 and len(controlled_review) == 1


@pytest.mark.parametrize('change', ['snapshot', 'hash', 'text', 'non_object'])
def test_tool_response_must_match_original_snapshot_and_content(monkeypatch, controlled_review, change):
    def alter(value):
        if change == 'snapshot': value['retrieval']['snapshot_id'] = 'other:1'
        if change == 'hash': value['retrieval']['documents_sha256'] = '0'*64
        if change == 'text': value['retrieval']['hits'][0]['text'] = 'Forged text'
        return [] if change == 'non_object' else value
    with retrieval_server(alter) as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(DecisionModel()).run(request())
    assert result['status'] == 'agent_tool_error'
    assert result['agent']['state'] == 'tool_error'
    assert len(seen) == 1 and not controlled_review


def test_model_cannot_choose_remote_url_or_change_snapshot(monkeypatch, controlled_review):
    class Escape:
        def complete(self, messages, tools):
            return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3,
                        url='https://example.invalid', snapshot_id='other:1'))
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Escape(), max_steps=1).run(request())
    assert not seen and result['status'] == 'agent_budget_exceeded'
    assert not controlled_review


@pytest.mark.parametrize('base', ['https://example.com', 'http://169.254.169.254:80', 'http://127.0.0.1:8765/other', 'http://u:p@127.0.0.1:8765'])
def test_tool_endpoint_is_configured_loopback_only(monkeypatch, base):
    model = DecisionModel(); monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'unavailable' and model.count == 0


def test_step_budget_stops_without_extra_model_or_review_call(monkeypatch, controlled_review):
    model = DecisionModel()
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(model, max_steps=1).run(request())
    assert result['status'] == 'agent_budget_exceeded' and result['usage']['budget_exhausted']
    assert model.count == 1 and len(seen) == 1 and not controlled_review


def test_model_exception_is_sanitized_and_usage_unknown(monkeypatch):
    class Broken:
        def complete(self, messages, tools): raise ModelError('secret-provider-key-do-not-copy')
    monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', 'http://127.0.0.1:8765')
    result = WorkspaceAgent(Broken()).run(request())
    assert result['status'] == 'provider_error' and result['usage']['model_calls'] == 1
    assert result['usage']['provider_usage_unknown']
    assert 'secret-provider-key' not in json.dumps(result)


def test_review_failure_stops_without_reentering_decision_loop(monkeypatch, controlled_review):
    def broken(*args, **kwargs): raise rag.RAGError('private provider error')
    monkeypatch.setattr(rag, 'review_assessments', broken)
    model = DecisionModel()
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'provider_error' and result['agent']['state'] == 'review_error'
    assert model.count == 3 and len(seen) == 1
    assert 'private provider error' not in json.dumps(result)


def test_all_observed_chunks_must_be_verified_before_report_review(monkeypatch, controlled_review):
    class Selective(DecisionModel):
        def complete(self, messages, tools):
            self.count += 1
            outputs = [json.loads(m['content']) for m in messages if m['role']=='tool']
            hits = next((o['hits'] for o in outputs if 'hits' in o), [])
            if self.count == 1: return call(self.count, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
            if self.count == 2: return call(self.count, 'verify_sources', dict(chunk_ids=[hits[0]['chunk_id']]))
            return call(self.count, 'submit_report', report(hits))
    req = request(documents=[dict(id='a', title='A', text='The measured accuracy was 86 percent.', source='a.md'),
                             dict(id='b', title='B', text='Another accuracy report disagrees.', source='b.md')])
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Selective()).run(req)
    assert result['status'] == 'agent_protocol_error' and not controlled_review
    assert result['agent']['state'] == 'invalid_report'


def test_existing_run_http_entry_accepts_workspace_context(monkeypatch, controlled_review):
    import lablens.api as api
    req = request(); monkeypatch.setattr(api, 'HTTPModel', lambda: DecisionModel())
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        response = TestClient(api.app).post('/api/run', json={'question': req.question,
                              'workspace_context': req.model_dump(exclude={'question', 'mode'})})
    assert response.status_code == 200 and response.json()['agent']['state'] == 'completed'
    assert len(seen) == 1


def test_rag_agent_http_entry_uses_same_existing_agent_branch(monkeypatch, controlled_review):
    import lablens.rag_api as route
    from lablens.api import app
    monkeypatch.setattr(route, 'HTTPModel', lambda: DecisionModel())
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        response = TestClient(app).post('/api/rag/answer', json=request().model_dump())
    assert response.status_code == 200 and response.json()['mode'] == 'agent'
    assert len(seen) == 1


def test_workspace_context_rejects_duplicate_documents_before_execution():
    req = request(); fields = req.model_dump(exclude={'question', 'mode'})
    fields['documents'] *= 2
    with pytest.raises(ValueError): WorkspaceContext.model_validate(fields)


def test_unapproved_embedding_strategy_cannot_trigger_a_provider_tool(monkeypatch, controlled_review):
    class Escalating:
        def complete(self, messages, tools):
            assert tools[0]['function']['parameters']['properties']['strategy']['enum'] == ['bm25']
            return call(1, 'search_workspace', dict(query='accuracy', strategy='dense', top_k=3))
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Escalating(), max_steps=1).run(request())
    assert not seen and result['status'] == 'agent_budget_exceeded'
    assert result['usage']['embedding_requests'] == 0


def test_model_timeout_is_explicit_and_does_not_trigger_tools_or_retry(monkeypatch):
    class TimeoutModel:
        def complete(self, messages, tools): raise ModelError('Provider request timed out; private-detail')
    monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', 'http://127.0.0.1:8765')
    result = WorkspaceAgent(TimeoutModel()).run(request())
    assert result['status'] == 'agent_timeout' and result['agent']['state'] == 'timeout'
    assert result['usage']['model_calls'] == 1 and result['agent']['tool_calls'] == 0
    assert 'private-detail' not in json.dumps(result)


def test_tool_call_budget_rejects_whole_batch_before_any_http(monkeypatch):
    class Batch:
        def complete(self, messages, tools):
            first, usage = call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
            second, _ = call(2, 'search_workspace', dict(query='baseline', strategy='bm25', top_k=3))
            first['tool_calls'] += second['tool_calls']
            return first, usage
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Batch(), max_calls=1).run(request())
    assert result['status'] == 'agent_budget_exceeded' and not seen
    assert result['agent']['tool_calls'] == 0


@pytest.mark.parametrize('failure', ['timeout', 'connection', 'malformed_json', 'wrong_snapshot'])
def test_unknown_embedding_tool_failure_preserves_known_model_usage(monkeypatch, failure):
    import httpx
    class DenseModel:
        def complete(self, messages, tools):
            return call(1, 'search_workspace', dict(query='accuracy', strategy='dense', top_k=3))
    def transport(req):
        if failure == 'timeout': raise httpx.ReadTimeout('private transport detail')
        if failure == 'connection': raise httpx.ConnectError('private transport detail')
        if failure == 'malformed_json': return httpx.Response(200, text='not JSON')
        return httpx.Response(200, json={'mode': 'extractive', 'retrieval': {'snapshot_id': 'wrong:1'}})
    monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', 'http://127.0.0.1:8765')
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = WorkspaceAgent(DenseModel(), client=client).run(request(strategy='dense'))
    assert result['status'] == ('agent_timeout' if failure == 'timeout' else 'agent_tool_error')
    assert result['usage']['model_calls'] == 1
    assert result['usage']['generation_tokens'] == {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}
    assert result['usage']['provider_usage_unknown'] is True
    for field in ('embedding_requests', 'embedded_inputs', 'embedding_cache_hits', 'embedding_tokens'):
        assert result['usage'][field] is None
    assert 'private transport' not in json.dumps(result)


@pytest.mark.parametrize('route', ['/api/run', '/api/rag/answer'])
@pytest.mark.parametrize('config', ['missing_key', 'invalid_port'])
def test_both_agent_entries_preserve_configuration_failure_trace_without_any_http(monkeypatch, route, config):
    import httpx
    from lablens.api import app
    monkeypatch.setenv('LLM_API_KEY', '' if config == 'missing_key' else 'test-unused-key')
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid:wrong' if config == 'invalid_port' else 'https://example.invalid')
    def unexpected(*args, **kwargs): raise AssertionError('No HTTP call is allowed for invalid configuration')
    monkeypatch.setattr(httpx, 'post', unexpected)
    req = request()
    payload = req.model_dump() if route.endswith('/answer') else {'question': req.question, 'workspace_context': req.model_dump(exclude={'question', 'mode'})}
    response = TestClient(app).post(route, json=payload)
    assert response.status_code == 503
    result = response.json()['detail']
    assert result['status'] == 'unavailable' and result['mode'] == 'agent'
    assert result['usage'] == rag._new_usage()
    assert result['agent']['state'] == 'configuration_error'
    assert result['agent']['steps'] == result['agent']['tool_calls'] == 0
    assert result['agent']['trace'][0]['event'] == 'stopped'
    assert result['agent']['snapshot_id'] == req.snapshot_id
    assert result['retrieval']['documents_sha256'] == rag.documents_sha256(req.documents)
    assert 'test-unused-key' not in response.text and 'example.invalid' not in response.text



def test_no_hits_allows_explicit_empty_verification_then_insufficient_report(monkeypatch, controlled_review):
    class EmptyVerifier(DecisionModel):
        def complete(self, messages, tools):
            self.count += 1
            if self.count == 1: return call(self.count, 'search_workspace', dict(query='nonmatchingterm', strategy='bm25', top_k=3))
            if self.count == 2: return call(self.count, 'verify_sources', {'chunk_ids': []})
            return call(self.count, 'submit_report', report([]))
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(EmptyVerifier()).run(request())
    assert result['status'] == 'refused' and result['agent']['state'] == 'insufficient_evidence'
    assert result['agent']['verified_chunk_ids'] == [] and result['citations'] == []
    verification = next(e['output'] for e in result['agent']['trace'] if e.get('event') == 'tool' and e.get('name') == 'verify_sources')
    assert verification['verified'] is True and verification['empty_evidence'] is True
    assert verification['scope'] == 'empty retrieval; no source verified'
    assert verification['chunk_ids'] == [] and len(seen) == 1 and len(controlled_review) == 1


def test_empty_verification_cannot_bypass_verification_of_retrieved_evidence(monkeypatch, controlled_review):
    class EmptyVerifier(DecisionModel):
        def complete(self, messages, tools):
            self.count += 1
            if self.count == 1: return call(self.count, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
            if self.count == 2: return call(self.count, 'verify_sources', {'chunk_ids': []})
            outputs = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
            hits = next(o['hits'] for o in outputs if 'hits' in o)
            return call(self.count, 'submit_report', report(hits))
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(EmptyVerifier()).run(request())
    assert result['status'] == 'agent_protocol_error' and result['agent']['verified_chunk_ids'] == []
    assert not controlled_review and len(seen) == 1
    assert any(e['event'] == 'tool_rejected' and e['name'] == 'verify_sources' for e in result['agent']['trace'])
    assert not any(e['event'] == 'tool' and e['name'] == 'verify_sources' for e in result['agent']['trace'])


def test_empty_verification_before_any_search_is_rejected(monkeypatch):
    class PrematureVerifier:
        def complete(self, messages, tools): return call(1, 'verify_sources', {'chunk_ids': []})
    result = WorkspaceAgent(PrematureVerifier(), max_steps=1).run(request())
    assert result['status'] == 'agent_budget_exceeded'
    assert result['agent']['verified_chunk_ids'] == []
    assert any(e['event'] == 'tool_rejected' for e in result['agent']['trace'])
