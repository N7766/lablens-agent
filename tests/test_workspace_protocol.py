"""Controlled transport/protocol checks, not evidence of provider compliance."""
import json

import httpx
import pytest

from lablens.conversation import ConversationBudget, ContextBudgetExceeded, estimated_units
from lablens.model import HTTPModel, ModelError
from lablens.workspace_agent import WorkspaceAgent
from test_workspace_agent import call, request


def test_workspace_transport_requests_structured_actions_and_budget_measures_actual_payload(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'controlled-unused-key')
    payloads = []

    def response(url, **kwargs):
        payloads.append(kwargs['json'])
        assert 'tools' not in kwargs['json'] and 'tool_choice' not in kwargs['json']
        assert kwargs['json']['response_format']['json_schema']['name'] == 'workspace_action'
        message = {'role': 'assistant', 'content': json.dumps({'decision': {'action': 'request_clarification', 'arguments': {'question': 'Which experiment?', 'choices': []}}})}
        usage = {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}
        return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': 'stop'}],
                                        'usage': usage}, request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx, 'post', response)
    result = WorkspaceAgent(HTTPModel()).run(request(question='What about that one?'))
    assert result['status'] == 'clarification_required'
    assert result['usage']['model_calls'] == len(payloads) == 1
    assert result['context']['context_budget']['calls'][0]['input_units'] == estimated_units(payloads[0])


def test_tool_choice_units_can_make_the_difference_between_fit_and_rejection():
    kwargs = dict(observed={}, verified={}, phase='decision_1', model_name='test')
    tools = [{'type': 'function', 'function': {'name': 'tool', 'parameters': {'type': 'object'}}}]
    baseline = ConversationBudget(window=65536)
    baseline.decision_messages('system', {'question': 'question'}, [], tools, **kwargs)
    exact_baseline = baseline.calls[0]['total_reserved_units']
    candidate = ConversationBudget(window=exact_baseline)
    with pytest.raises(ContextBudgetExceeded):
        candidate.decision_messages('system', {'question': 'question'}, [], tools, tool_choice='required', **kwargs)
    assert candidate.calls[0]['total_reserved_units'] > exact_baseline
    assert candidate.calls[0]['fits'] is False


@pytest.mark.parametrize('finish_reason,expected', [('stop', 'stop'), ('length', 'length'),
    ('content_filter', 'content_filter'), ('tool_calls', 'tool_calls'),
    ('private-provider-detail', 'unknown'), (None, 'unknown'), ({'secret': 'hidden'}, 'unknown')])
def test_non_tool_decision_retains_safe_shape_without_model_text_or_retry(finish_reason, expected):
    class NonToolModel:
        def __init__(self): self.calls = 0
        def complete(self, messages, tools):
            self.calls += 1
            return {'role': 'assistant', 'content': 'sensitive unaccepted model prose',
                    'finish_reason': finish_reason}, {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}

    model = NonToolModel()
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_protocol_error' and model.calls == 1
    rejected = next(event for event in result['agent']['trace'] if event['event'] == 'model_decision_rejected')
    assert rejected['output'] == dict(finish_reason=expected, has_tool_calls=False, tool_call_count=0, has_content=True)
    assert result['usage']['model_calls'] == 1 and result['usage']['provider_usage_unknown'] is False
    assert result['agent']['tool_calls'] == 0 and result['assessments'] == []
    serialized = json.dumps(result)
    assert 'sensitive unaccepted model prose' not in serialized
    assert 'private-provider-detail' not in serialized and 'hidden' not in serialized


def test_malformed_tool_batch_logs_only_protocol_shape():
    class DuplicateModel:
        def complete(self, messages, tools):
            message, usage = call(1, 'search_workspace', {'query': 'sensitive-invalid-argument'})
            message['tool_calls'] *= 2
            message['finish_reason'] = 'tool_calls'
            return message, usage

    result = WorkspaceAgent(DuplicateModel()).run(request())
    assert result['status'] == 'agent_protocol_error'
    rejected = next(event for event in result['agent']['trace'] if event['event'] == 'model_decision_rejected')
    assert rejected['output'] == dict(finish_reason='tool_calls', has_tool_calls=True,
                                                       tool_call_count=2, has_content=False)
    assert 'sensitive-invalid-argument' not in json.dumps(result)


def test_structured_action_does_not_retry_or_accept_noncompliant_provider(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'controlled-unused-key')
    calls = []

    def response(url, **kwargs):
        calls.append(kwargs['json'])
        assert 'tools' not in kwargs['json'] and 'tool_choice' not in kwargs['json']
        assert kwargs['json']['response_format']['json_schema']['name'] == 'workspace_action'
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'unaccepted answer'},
                    'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}},
                    request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx, 'post', response)
    result = WorkspaceAgent(HTTPModel()).run(request())
    assert len(calls) == result['usage']['model_calls'] == 1
    assert result['status'] == 'agent_protocol_error'
    rejected = next(event for event in result['agent']['trace'] if event['event'] == 'model_decision_rejected')
    assert rejected['output']['finish_reason'] == 'stop'
    assert result['answer'] == '' and 'unaccepted answer' not in json.dumps(result)


@pytest.mark.parametrize('tools,choice', [([], 'required'), ([{'type': 'function'}], 'unsupported')])
def test_invalid_tool_choice_stops_before_transport(monkeypatch, tools, choice):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'controlled-unused-key')
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: pytest.fail('Unexpected HTTP request'))
    with pytest.raises(ModelError, match='tool_choice'):
        HTTPModel().complete([], tools, tool_choice=choice)
