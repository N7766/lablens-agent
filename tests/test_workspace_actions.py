"""Controlled HTTP responses test orchestration, not live model answer quality."""
import json
from types import SimpleNamespace

import httpx
import pytest

from lablens.conversation import ConversationBudget, ContextBudgetExceeded, CONVERSATION_INSTRUCTIONS, estimated_units
from lablens.model import HTTPModel, workspace_action_payload
from lablens.workspace_agent import WorkspaceAgent
from lablens import workspace_agent, rag
from lablens.cancellation import CancellationRegistry
from test_conversation import context
from test_workspace_agent import request, call, DecisionModel, retrieval_server, controlled_review


@pytest.fixture
def configured_model(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'unused-controlled-key')
    return HTTPModel()


def response(url, decision, *, finish='stop', usage=None):
    return httpx.Response(200, request=httpx.Request('POST', url), json=dict(
        choices=[dict(message=dict(role='assistant', content=json.dumps(decision)), finish_reason=finish)],
        usage=usage or dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)))


def test_combined_action_retrieves_then_verifies_every_source_with_one_less_decision(monkeypatch, configured_model, controlled_review):
    payloads, timeouts = [], []
    def provider(url, **kwargs):
        payload = kwargs['json']; payloads.append(payload); timeouts.append(kwargs['timeout'])
        assert 'tools' not in payload and 'tool_choice' not in payload
        schema = payload['response_format']['json_schema']['schema']
        choices = schema['properties']['decision']['anyOf']
        names = [item['properties']['action']['enum'][0] for item in choices]
        assert '$ref' not in json.dumps(schema)  # composed refs cannot resolve against the wrong tool
        if len(payloads) == 1:
            assert set(names) == {'search_workspace', 'request_clarification'}
            action, args = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            assert 'verify_and_submit_report' in names and 'verify_sources' not in names
            outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                       if m['role'] == 'user' and 'untrusted_tool_result' in m['content']]
            evidence = outputs[-1]
            assert all('text' not in hit for hit in evidence['hits'])
            assert all(item['quote'] for item in evidence['passages'].values())
            action = 'verify_and_submit_report'
            args = dict(assessments=[dict(statement='The measured accuracy was 86 percent.', verdict='supported',
                explanation='This is documented in the supplied local fixture.', basis='historical_observation',
                evidence_ids=list(evidence['passages']))])
        return response(url, dict(decision=dict(action=action, arguments=args)))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request(conversation_context=context().model_dump()))
    assert result['status'] == 'answered' and len(seen) == 1 and len(payloads) == 2
    assert result['usage']['model_calls'] == 3  # two choices + one separate review
    assert result['agent']['decision_protocol'] == 'json_schema_action_v1'
    assert result['agent']['review_reserve_seconds'] == 20
    assert all(0 < value <= min(configured_model.timeout_seconds, 55) for value in timeouts)
    validation = next(e['output'] for e in result['agent']['trace'] if e['event'] == 'source_validation')
    assert validation['selection'] == 'deterministic_all_observed_sources'
    assert validation['semantic_entailment_verified'] is False
    assert validation['chunk_ids'] == [hit['chunk_id'] for hit in result['retrieval']['hits']]
    raw_search = next(e['output'] for e in result['agent']['trace'] if e.get('name') == 'search_workspace' and e['event'] == 'tool')
    assert all(hit['text'] for hit in raw_search['hits'])  # trace is not compressed
    assert [item['input_units'] for item in result['context']['context_budget']['calls'][:2]] == [estimated_units(p) for p in payloads]
    assert result['user_scope']['authority'] == 'user_provided_context_not_verified_evidence'
    assert '只分析验证集，不把它称为测试集' in result['answer']
    assert '非证据结论' in result['answer'] and '可能已被本轮更正覆盖' in result['answer']
    assert controlled_review[0]['context_data']['current_question'] == result['question']


@pytest.mark.parametrize('decision,finish', [
    ({}, 'stop'), ({'decision': {'action': 'shell', 'arguments': {}}}, 'stop'),
    ({'decision': {'action': 'request_clarification', 'arguments': '{}'}}, 'stop'),
    ({'decision': {'action': ['request_clarification'], 'arguments': {}}}, 'stop'),
    ({'decision': {'action': 'request_clarification', 'arguments': {}}, 'extra': 'bad'}, 'stop'),
    ({'decision': {'action': 'request_clarification', 'arguments': {}}}, 'length'),
])
def test_invalid_structured_decision_retains_known_usage_without_retry(monkeypatch, configured_model, decision, finish):
    calls = []
    def provider(url, **kwargs):
        calls.append(kwargs)
        return response(url, decision, finish=finish)
    monkeypatch.setattr(httpx, 'post', provider)
    result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'agent_protocol_error'
    assert len(calls) == result['usage']['model_calls'] == 1
    assert result['usage']['generation_tokens']['total_tokens'] == 5
    assert result['usage']['provider_usage_unknown'] is False
    assert result['agent']['tool_calls'] == 0


def test_context_guard_checks_transformed_schema_payload_before_model_transport():
    tools = [dict(type='function', function=dict(name='request_clarification', description='clarify',
             parameters=dict(type='object', properties=dict(question=dict(type='string')), required=['question'])))]
    def builder(messages, tools):
        payload = workspace_action_payload(messages, tools, model='controlled')
        payload['response_format']['json_schema']['description'] = 'x' * 50000
        return payload
    budget = ConversationBudget(window=12000)
    with pytest.raises(ContextBudgetExceeded):
        budget.decision_messages('system', {'question': 'current'}, [], tools, observed={}, verified={},
                                 phase='decision_1', payload_builder=builder)
    assert budget.calls[-1]['input_units'] > 50000


def test_current_correction_is_explicitly_prioritized_in_decision_review_and_scope_note():
    ctx = context(state=dict(goal='本地合成评测', constraints=['不得推断生产性能'], focus='old-checkpoint'))
    budget = ConversationBudget(ctx)
    question = '更正：只看 new-checkpoint，不再看 old-checkpoint。'
    assert 'current question overrides an older pinned focus' in CONVERSATION_INSTRUCTIONS
    assert budget.review_context(question)['current_question'] == question
    note = budget.scope_note(question)
    assert note['saved_focus'] == 'old-checkpoint'  # persist input truthfully; don't manufacture a parsed memory update
    assert note['current_question'] == question
    assert 'not an assertion that they still apply' in note['precedence']


def test_timeout_has_no_native_parser_fallback_or_second_request(monkeypatch, configured_model):
    calls = []
    def timeout(url, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout('controlled timeout')
    monkeypatch.setattr(httpx, 'post', timeout)
    monkeypatch.setattr(configured_model, 'complete', lambda *a, **kw: pytest.fail('No native fallback'))
    result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'agent_timeout'
    assert result['usage']['model_calls'] == len(calls) == 1
    assert result['usage']['provider_usage_unknown'] is True


def test_exploration_cannot_consume_final_review_reserve(monkeypatch, configured_model):
    elapsed, calls = [0.0], []
    monkeypatch.setattr(workspace_agent, 'time', SimpleNamespace(monotonic=lambda: elapsed[0]))
    def provider(url, **kwargs):
        calls.append(kwargs)
        elapsed[0] = 55
        return response(url, dict(decision=dict(action='search_workspace',
            arguments=dict(query='accuracy', strategy='bm25', top_k=3))))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'agent_timeout' and result['usage']['budget_exhausted'] is True
    assert result['usage']['model_calls'] == len(calls) == 1
    assert len(seen) == 1 and result['latency_ms'] == 55000
    assert 'decision budget' in result['error']


def test_clarification_remains_available_when_only_final_decision_time_remains(monkeypatch, configured_model):
    elapsed, calls = [0.0], []
    monkeypatch.setattr(workspace_agent, 'time', SimpleNamespace(monotonic=lambda: elapsed[0]))
    def provider(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            elapsed[0] = 40
            return response(url, dict(decision=dict(action='search_workspace',
                arguments=dict(query='accuracy', strategy='bm25', top_k=3))))
        names = {item['properties']['action']['enum'][0] for item in kwargs['json']['response_format']['json_schema']['schema']['properties']['decision']['anyOf']}
        assert names == {'verify_and_submit_report', 'request_clarification'}
        assert kwargs['timeout'] == 15
        return response(url, dict(decision=dict(action='request_clarification',
            arguments=dict(question='Which of these observations do you mean?', choices=['First', 'Second']))))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'clarification_required' and len(calls) == 2
    assert result['usage']['model_calls'] == 2


@pytest.mark.parametrize('configured_timeout,expected_second_timeout', [(45, 45), (90, 50)])
def test_second_decision_can_use_available_time_without_spending_review_reserve(monkeypatch, configured_model,
                                                                              configured_timeout, expected_second_timeout):
    elapsed, calls = [0.0], []
    configured_model.timeout_seconds = configured_timeout
    monkeypatch.setattr(workspace_agent, 'time', SimpleNamespace(monotonic=lambda: elapsed[0]))
    def provider(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            elapsed[0] = 5
            return response(url, dict(decision=dict(action='search_workspace',
                arguments=dict(query='accuracy', strategy='bm25', top_k=3))))
        assert kwargs['timeout'] == expected_second_timeout
        assert kwargs['timeout'] > 30
        assert elapsed[0] + kwargs['timeout'] + 20 <= 75
        elapsed[0] += 31  # A decision taking over 30 seconds can still complete safely.
        return response(url, dict(decision=dict(action='request_clarification',
            arguments=dict(question='Which observation do you mean?', choices=['First', 'Second']))))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'clarification_required' and len(calls) == 2
    assert result['agent']['budget_seconds'] == 75 and result['agent']['review_reserve_seconds'] == 20
    assert result['latency_ms'] == 36000


def test_cancellation_after_model_preserves_known_usage_and_runs_no_tool(monkeypatch):
    registry = CancellationRegistry()
    class CancelAfterResponse:
        def complete(self, messages, tools):
            registry.cancel('controlled-model-cancel')
            return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
    with registry.execution('controlled-model-cancel'):
        result = WorkspaceAgent(CancelAfterResponse()).run(request())
    assert result['status'] == 'agent_cancelled' and result['agent']['state'] == 'cancelled'
    assert result['usage']['model_calls'] == 1 and result['usage']['generation_tokens']['total_tokens'] == 5
    assert result['usage']['provider_usage_unknown'] is False
    assert result['agent']['tool_calls'] == 0 and result['answer'] == ''


def test_cancellation_after_review_retains_usage_and_evidence_but_withholds_answer(monkeypatch, controlled_review):
    registry = CancellationRegistry()
    review = rag.review_assessments
    def cancelled_review(*args, **kwargs):
        result = review(*args, **kwargs)
        registry.cancel('controlled-review-cancel')
        return result
    monkeypatch.setattr(rag, 'review_assessments', cancelled_review)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        with registry.execution('controlled-review-cancel'):
            result = WorkspaceAgent(DecisionModel()).run(request())
    assert result['status'] == 'agent_cancelled' and result['answer'] == ''
    assert result['usage']['model_calls'] == 4
    assert result['usage']['generation_tokens']['total_tokens'] == 17
    assert len(result['retrieval']['hits']) == len(result['agent']['verified_chunk_ids']) == 1
    assert not any(e['event'] == 'review_completed' for e in result['agent']['trace'])
