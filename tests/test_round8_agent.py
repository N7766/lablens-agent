"""Real loopback retrieval and scripted provider responses, no external inference."""
import json

import httpx
import pytest

from lablens.workspace_agent import WorkspaceAgent
from test_workspace_actions import configured_model, response
from test_workspace_agent import request, retrieval_server


@pytest.mark.parametrize('case', ['wrong_peer', 'unrelated_setting', 'matching_setting'])
def test_binding_survives_agent_review_and_preserves_auditable_original(monkeypatch, configured_model, case):
    monkeypatch.setenv('LLM_MODEL', 'controlled-round8-model')
    wrong = case != 'matching_setting'
    text = ('Maple: latency 12 ms.' if case == 'wrong_peer' else
            'Current configuration: worker_retries=2.' if case == 'matching_setting' else
            'Archived alert: badge 2; final delivery complete.')
    statement = 'Birch latency is 12 ms.' if case == 'wrong_peer' else 'The configured worker_retries=2.'
    explanation = 'Maple latency is 12 ms.' if case == 'wrong_peer' else 'The current worker_retries=2.'
    question = 'Compare Birch and Maple latency.' if case == 'wrong_peer' else 'What is the configured worker_retries?'
    basis = 'documented_fact' if case == 'wrong_peer' else 'current_configuration'
    sent = []
    def provider(url, **kwargs):
        payload = kwargs['json']; sent.append(payload)
        if payload['response_format']['json_schema']['name'] == 'evidence_review_by_id':
            data = json.loads(payload['messages'][-1]['content'])
            assert data['server_review_contract']['evidence_binding']['semantic_entailment_verified'] is False
            rows = [{k: v for k, v in row.items() if k != 'statement'} for row in data['untrusted_draft']['assessments']]
            return response(url, {'assessments': rows})
        if len(sent) == 1:
            action, args = 'search_workspace', {'query': text, 'strategy': 'bm25', 'top_k': 1}
        else:
            outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                       if m['role'] == 'user' and 'untrusted_tool_result' in m['content']]
            action = 'verify_and_submit_report'
            args = {'assessments': [dict(statement=statement, explanation=explanation, verdict='supported',
                                        basis=basis, evidence_ids=list(outputs[-1]['passages']))]}
        return response(url, {'decision': {'action': action, 'arguments': args}})
    monkeypatch.setattr(httpx, 'post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request(question=question, documents=[
            dict(id='record', title='Observed record', text=text, source='synthetic:record')]))
    assert len(sent) == result['usage']['model_calls'] == 3
    assert len(seen) == 1 and result['agent']['version'] == 'workspace-agent-v9'
    assert result['usage']['provider_usage_unknown'] is False
    assert result['status'] == ('refused' if wrong else 'answered')
    quality = result['assessment_validation']['selected_evidence_checks']
    assert quality['semantic_entailment_verified'] is False
    assert quality['evidence_binding']['semantic_entailment_verified'] is False
    assert bool(quality['findings']) is wrong
    row = result['assessments'][0]
    assert row['statement'] == statement and row['quotes'][0]['quote'] == text
    if wrong:
        assert row['verdict'] == 'insufficient' and row['explanation'] != explanation
        assert quality['withheld_originals'] == [dict(assessment_index=0,
            original_verdict='supported', original_explanation=explanation)]
    else:
        assert row['explanation'] == explanation and quality['withheld_originals'] == []
    trace_quality = next(e['output'] for e in result['agent']['trace'] if e['event'] == 'evidence_quality')
    assert trace_quality == quality
