"""Scripted decisions verify bounded action policy; no external model inference."""
import json

import httpx
import pytest

from lablens.workspace_agent import WorkspaceAgent
from lablens import rag
from test_workspace_actions import configured_model, response
from test_workspace_agent import request, call, retrieval_server, controlled_review

QUESTION = '给出测量准确率的结论，并说明还缺少哪些证据。'


@pytest.mark.parametrize('no_hits', [False, True])
def test_explicit_answer_and_gap_request_removes_clarify_after_lookup(monkeypatch, configured_model, controlled_review, no_hits):
    payloads = []
    def provider(url, **kwargs):
        payload = kwargs['json']; payloads.append(payload)
        actions = {p['properties']['action']['enum'][0] for p in payload['response_format']['json_schema']['schema']['properties']['decision']['anyOf']}
        if len(payloads) == 1:
            assert actions == {'search_workspace', 'request_clarification'}
            action, arguments = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            assert 'request_clarification' not in actions
            outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages'] if m['role']=='user' and 'untrusted_tool_result' in m['content']]
            action = 'verify_and_submit_report'
            arguments = dict(assessments=[dict(statement='The measured accuracy was 86 percent.',
                verdict='insufficient' if no_hits else 'supported',
                explanation='No observation is available.' if no_hits else 'The supplied fixture documents this observation.',
                basis='historical_observation', evidence_ids=list(outputs[-1]['passages']))])
        return response(url, dict(decision=dict(action=action, arguments=arguments)))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request(question=QUESTION, **({'documents': []} if no_hits else {})))
    assert result['status'] == ('refused' if no_hits else 'answered')
    assert len(payloads) == 2 and len(seen) == 1 and len(controlled_review) == 1
    assert result['usage']['model_calls'] == 3
    decisions = [e for e in result['agent']['trace'] if e['event'] == 'model_decision']
    assert decisions[0]['clarification_policy']['allow_clarification'] is True
    assert decisions[1]['clarification_policy']['allow_clarification'] is False
    assert decisions[1]['clarification_policy']['evidence_sufficiency_verified'] is False


@pytest.mark.parametrize('native', [True, False])
def test_disallowed_clarification_is_rejected_without_repair_or_fabricated_answer(monkeypatch, configured_model, native):
    calls = []
    def decision(messages, tools):
        calls.append(tools)
        if len(calls) == 1:
            return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
        return call(2, 'request_clarification', {'question': 'Can you supply more evidence?'})
    class Native:
        complete = staticmethod(decision)
    def provider(url, **kwargs):
        message, usage = decision(None, None)
        picked = message['tool_calls'][0]['function']
        return response(url, dict(decision=dict(action=picked['name'], arguments=json.loads(picked['arguments']))), usage=usage)
    monkeypatch.setattr(httpx, 'post', provider)
    monkeypatch.setattr(rag, 'review_assessments', lambda *a, **k: pytest.fail('No review for disallowed action'))
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Native() if native else configured_model).run(request(question=QUESTION))
    assert len(calls) == result['usage']['model_calls'] == 2
    assert len(seen) == 1 and result['status'] == 'agent_protocol_error'
    assert result['answer'] == '' and 'clarification' not in result
    assert result['usage']['provider_usage_unknown'] is False
    assert result['usage']['generation_tokens']['total_tokens'] == 10
    assert not any(e['event'] == 'tool' and e.get('name') == 'request_clarification' for e in result['agent']['trace'])


def test_explicit_clarify_first_keeps_action_after_retrieval(monkeypatch, configured_model):
    calls = []
    def provider(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            action, arguments = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            actions = [p['properties']['action']['enum'][0] for p in kwargs['json']['response_format']['json_schema']['schema']['properties']['decision']['anyOf']]
            assert 'request_clarification' in actions
            action, arguments = 'request_clarification', dict(question='Which measurement do you mean?')
        return response(url, dict(decision=dict(action=action, arguments=arguments)))
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request(question=QUESTION + '先向我澄清。'))
    assert result['status'] == 'clarification_required' and len(calls) == 2
