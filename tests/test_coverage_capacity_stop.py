"""Post-seal regression: a full report cannot trigger another billed repair call."""
import copy
import json

import pytest

from lablens.model import HTTPModel
from lablens.workspace_agent import WorkspaceAgent
from test_workspace_agent import (DecisionModel, call, report, request, retrieval_server,
                                  controlled_review)


def full_draft(hits):
    row = report(hits)['assessments'][0]
    return {'assessments': [copy.deepcopy(row) for _ in range(8)]}


class NativeFullDraft(DecisionModel):
    def complete(self, messages, tools):
        if self.count < 2:
            return super().complete(messages, tools)
        self.count += 1
        assert self.count == 3, 'Unexpected extra model call after the capacity error'
        hits = next(json.loads(m['content'])['hits'] for m in messages
                    if m['role'] == 'tool' and 'hits' in json.loads(m['content']))
        return call(self.count, 'submit_report', full_draft(hits))


class StructuredFullDraft(HTTPModel):
    """Scripted decisions only; no constructor secrets, HTTP or provider requests."""
    def __init__(self):
        self.model = 'controlled-structured-capacity'
        self.timeout_seconds = 30
        self.count = 0

    def choose_workspace_action(self, payload, timeout_seconds=None):
        self.count += 1
        if self.count == 1:
            return call(self.count, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
        assert self.count == 2, 'Unexpected extra structured model call'
        outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                   if m['role'] == 'user' and 'untrusted_tool_result' in m['content']]
        # Structured projections keep exact text in passages, not duplicated in hits.
        row = dict(statement='The measured accuracy was 86 percent.', verdict='supported',
                   explanation='The report contains this observation.', basis='historical_observation',
                   evidence_ids=list(outputs[-1]['passages'])[:1])
        return call(self.count, 'verify_and_submit_report',
                    {'assessments': [copy.deepcopy(row) for _ in range(8)]})


@pytest.mark.parametrize(('model_type', 'expected_calls'), [(NativeFullDraft, 3), (StructuredFullDraft, 2)])
def test_full_missing_overall_report_stops_before_retry_or_review(monkeypatch, controlled_review, model_type, expected_calls):
    model = model_type()
    req = request(question='Compare Dune and Reed and give overall verdicts.')
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(model).run(req)
    assert result['status'] == 'agent_protocol_error'
    assert result['agent']['state'] == 'coverage_contract_error'
    assert result['answer'] == '' and result['assessments'] == []
    assert model.count == result['usage']['model_calls'] == expected_calls
    assert len(seen) == 1 and not controlled_review
    trace = result['agent']['trace']
    rejected = [event for event in trace if event['event'] == 'tool_rejected']
    assert len(rejected) == 1
    assert rejected[0]['output']['reason'] == 'coverage_contract_capacity'
    assert rejected[0]['output']['assessment_count'] == 8
    assert rejected[0]['output']['missing_overall_slots'] == ['Dune 的本次验收总判定。', 'Reed 的本次验收总判定。']
    assert not any(event['event'] == 'review_started' for event in trace)
    assert any(event.get('outcome') == 'rejected' for event in trace if event['event'] == 'stage_finished')
    assert result['usage']['provider_usage_unknown'] is False
