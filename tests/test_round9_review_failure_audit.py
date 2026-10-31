"""Post-seal diagnosis repair; controlled responses cannot recover lost old replies."""
import hashlib
import json

import httpx
import pytest

from lablens.workspace_agent import WorkspaceAgent, _review_response_observation
from test_workspace_actions import configured_model, response
from test_workspace_agent import request, retrieval_server


@pytest.mark.parametrize('kind', ['missing_partition', 'overlap', 'unknown_id', 'invalid_json', 'length', 'long_invalid', 'message_null', 'message_string', 'message_list', 'choice_null'])
def test_failed_review_keeps_bounded_unaccepted_response_without_retry(monkeypatch, configured_model, kind):
    monkeypatch.setenv('LLM_MODEL', 'controlled-review-failure')
    sent, bodies = [], []
    def provider(url, **kwargs):
        payload = kwargs['json']; sent.append(payload)
        if payload['response_format']['json_schema']['name'] == 'evidence_review_by_id':
            data = json.loads(payload['messages'][-1]['content'])
            rows = [{k: v for k, v in row.items() if k != 'statement'} for row in data['untrusted_draft']['assessments']]
            value = {'retained_assessment_ids': [], 'assessments': rows}
            if kind == 'missing_partition': value.pop('retained_assessment_ids')
            if kind == 'overlap': value['retained_assessment_ids'] = ['a1']
            if kind == 'unknown_id': rows[0]['assessment_id'] = 'a999'
            if kind == 'long_invalid': rows[0]['explanation'] = '证据' * 12000
            content = 'bad {' if kind == 'invalid_json' else json.dumps(value, ensure_ascii=False)
            bodies.append(content)
            choice = {'finish_reason': 'length' if kind == 'length' else 'stop', 'message': {'content': content}}
            malformed = {'message_null': None, 'message_string': 'bad message', 'message_list': []}
            if kind in malformed: choice['message'] = malformed[kind]
            if kind == 'choice_null': choice = None
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'choices': [choice],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}})
        if len(sent) == 1:
            action, args = 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3)
        else:
            outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                       if m['role'] == 'user' and 'untrusted_tool_result' in m['content']]
            action, args = 'verify_and_submit_report', dict(assessments=[dict(
                statement='The measured accuracy was 86 percent.', verdict='supported',
                explanation='The report records 86 percent.', basis='historical_observation',
                evidence_ids=list(outputs[-1]['passages']))])
        return response(url, {'decision': {'action': action, 'arguments': args}})
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request())
    assert result['status'] == 'provider_error' and result['answer'] == ''
    assert len(sent) == result['usage']['model_calls'] == 3
    trace = result['agent']['trace']
    errors = [e['output'] for e in trace if e['event'] == 'tool_rejected' and e['output'].get('phase') == 'review']
    assert len(errors) == 1
    error = errors[0]; observation = error['response_observation']; content = bodies[0]
    assert error['error_status'] == 'invalid_citations'
    assert not any(e['event'] == 'review_completed' for e in trace)
    assert 'generation_review' not in result
    if kind.startswith('message_') or kind == 'choice_null':
        assert observation == {'content_observed': False, 'accepted_report': False}
        assert error['validation_detail'] is None
        return
    assert observation['content_observed'] and observation['accepted_report'] is False
    assert observation['finish_reason'] == ('length' if kind == 'length' else 'stop')
    assert observation['content_sha256'] == hashlib.sha256(content.encode()).hexdigest()
    assert observation['content_bytes'] == len(content.encode())
    assert len(observation['content_prefix'].encode()) <= 16384
    assert content.startswith(observation['content_prefix'])
    assert observation['content_truncated'] == (kind == 'long_invalid')
    if kind not in ['length', 'invalid_json']:
        assert error['validation_detail']['stage'] == 'restore_or_validate'
        assert error['validation_detail']['exception_type'] in {'ValueError', 'ValidationError'}
    assert not any(e['event'] == 'review_completed' for e in trace)
    assert 'generation_review' not in result


def test_observer_does_not_invent_content_and_caps_metadata():
    assert _review_response_observation({'content': None}) == {
        'version': 'review-failure-observation-v1', 'finish_reason': None,
        'content_observed': False, 'accepted_report': False}
    event = {'content': '中' * 10000, 'finish_reason': 'x' * 1000, 'Authorization': 'DO NOT COPY'}
    value = _review_response_observation(event)
    assert value['content_prefix'] == '中' * (16384 // 3)
    assert len(value['finish_reason']) == 80 and 'Authorization' not in value
    assert value['content_truncated'] is True
