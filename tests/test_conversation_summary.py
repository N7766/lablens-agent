"""Exact user-history preservation, not a model semantic-quality evaluation."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from lablens.conversation import ConversationContext, ConversationBudget, ContextBudgetExceeded
from lablens.conversation_summary import (AUTHORITY, MAX_BYTES, ConversationSummary, SummaryCapacityExceeded,
                                         build_summary, canonical)
from test_conversation import context
from test_workspace_agent import request, call, DecisionModel, retrieval_server, controlled_review
from lablens.workspace_agent import WorkspaceAgent


def turn(seq, question='不代表真实线上流量。', snapshot='workspace:1'):
    return SimpleNamespace(seq=seq, question=question, snapshot_id=snapshot)


def test_canonical_cross_language_hash_preserves_unicode_escapes_and_exact_negation():
    summary = build_summary(turns=[turn(1, ' 中文 😀 \n\x0f\\"不要将验证集称为测试集。 '),
                                   turn(2, '更正：保留 SFT 基线；仍需查证 DPO 下降原因。', 'workspace:2')])
    assert summary.messages[0].question.startswith(' 中文 😀 \n\x0f')
    assert summary.source_sha256 == 'ed3c60612dbf4928bf4ce346c77c6e46580ef8f0ed6a1afe09b70daf0df27386'
    assert summary.authority == AUTHORITY
    assert summary.omitted_assistant_answers == 2
    assert len(canonical(summary.model_dump())) <= MAX_BYTES


@pytest.mark.parametrize('mutation', ['question', 'snapshot', 'sequence', 'order', 'source_hash', 'range', 'omission', 'authority', 'fact'])
def test_strict_summary_rejects_tampering_and_unverified_fact_fields(mutation):
    data = build_summary(turns=[turn(1), turn(2)]).model_dump()
    if mutation == 'question': data['messages'][0]['question'] = '现在可以称为线上流量。'
    if mutation == 'snapshot': data['messages'][0]['snapshot_id'] = 'workspace:999'
    if mutation == 'sequence': data['messages'][0]['seq'] = 2
    if mutation == 'order': data['messages'].reverse()
    if mutation == 'source_hash': data['source_sha256'] = 'a'*64
    if mutation == 'range': data['source_range']['last_seq'] = 999
    if mutation == 'omission': data['omitted_assistant_answers'] = 1
    if mutation == 'authority': data['authority'] = 'verified_facts'
    if mutation == 'fact': data['verified_facts'] = ['DPO caused degradation']
    with pytest.raises(ValueError): ConversationSummary.model_validate(data)


def test_summary_precedes_recent_turns_covers_omitted_count_and_survives_snapshot_roundtrip():
    summary = build_summary(turns=[turn(1), turn(2)])
    ctx = context(4, omitted_turns=2, turns=context(4).model_dump()['turns'][2:], summary=summary.model_dump())
    frozen = ctx.model_dump_json()
    restored = ConversationContext.model_validate_json(frozen)
    assert restored.summary == ctx.summary
    assert restored.summary.source_sha256 == summary.source_sha256
    for change in ({'omitted_turns': 3}, {'revision': 2}):
        with pytest.raises(ValueError): ConversationContext.model_validate({**ctx.model_dump(), **change})
    overlap = ctx.model_dump(); overlap['turns'][0]['seq'] = 2
    with pytest.raises(ValueError): ConversationContext.model_validate(overlap)


def test_budget_drops_assistant_drafts_but_retains_every_user_correction_and_unfinished_request():
    ctx = context(20)
    ctx.turns[0].question = '不得将本地合成流量称为线上流量；尚未解决超时根因。'
    ctx.turns[1].question = '更正：SFT 基线已经存在，继续核验原因，不要重跑训练。'
    for previous in ctx.turns:
        previous.answer = '未经核验的旧回答。'*300
    original = copy.deepcopy(ctx.model_dump())
    budget = ConversationBudget(ctx, window=27000)
    messages = budget.decision_messages('system', {'question': '本轮只解释超时。'}, [], [],
        observed={}, verified={}, phase='decision_1')
    request = json.loads(messages[-1]['content'])
    summary = request['conversation_user_summary']
    assert budget.calls[0]['dropped_turns'] > 0
    assert summary['messages'][0]['question'] == ctx.turns[0].question
    assert summary['messages'][1]['question'] == ctx.turns[1].question
    assert not any('answer' in entry for entry in summary['messages'])
    users = [json.loads(m['content']) for m in messages if m['role'] == 'user']
    retained = [item['turn_seq'] for item in users if 'previous_user_message' in item]
    assert [entry['seq'] for entry in summary['messages']] + retained == list(range(1, 21))
    assert request['question'] == '本轮只解释超时。'
    assert request['retained_source_index'] == request['verified_source_ids'] == []
    assert ctx.model_dump() == original
    review = budget.review_context('本轮只解释超时。')
    assert review['conversation_user_summary']['messages'][0]['question'] == ctx.turns[0].question
    assert review['previous_answers_are_evidence'] is False
    assert review['recent_user_messages'][-1]['question'] == ctx.turns[-1].question


def test_admitted_old_summary_and_new_budget_evictions_merge_in_order_without_rewriting_input():
    prior = build_summary(turns=[turn(1, '不要使用外部付费模型。'), turn(2, '仍需核对历史配置。')])
    recent = context(20).model_dump()['turns']
    for item in recent:
        item['seq'] += 2
        item['answer'] = 'old draft ' * 390
    ctx = context(20, revision=23, omitted_turns=2, turns=recent, summary=prior.model_dump())
    before = ctx.model_dump_json()
    budget = ConversationBudget(ctx, window=23000)
    messages = budget.decision_messages('system', {'question': '继续核验'}, [], [], observed={}, verified={}, phase='decision')
    current = json.loads(messages[-1]['content'])
    summary = current['conversation_user_summary']
    assert summary['messages'][:2] == prior.model_dump()['messages']
    assert summary['source_range']['first_seq'] == 1
    assert budget.metadata['unavailable_legacy_user_turns'] == 0
    assert ctx.model_dump_json() == before


def test_summary_capacity_never_truncates_a_clause_or_silently_forgets_user_context():
    with pytest.raises(SummaryCapacityExceeded): build_summary(turns=[turn(i) for i in range(1, 102)])
    with pytest.raises(SummaryCapacityExceeded): build_summary(turns=[turn(i, '中'*2000) for i in range(1, 5)])
    ctx = context(20)
    for previous in ctx.turns: previous.question = '不'*2000
    budget = ConversationBudget(ctx, window=1000000)
    with pytest.raises(ContextBudgetExceeded):
        budget.decision_messages('system', {'question': '继续'}, [], [], observed={}, verified={}, phase='decision_1')
    assert budget.calls[-1]['fits'] is False
    assert budget.calls[-1]['reason'] == 'exact_user_summary_capacity'
    assert budget.calls[-1]['user_messages_truncated'] is False


def test_legacy_missing_user_history_is_explicit_and_never_fabricated():
    budget = ConversationBudget(context(2, omitted_turns=9))
    assert budget.metadata['unavailable_legacy_user_turns'] == 9
    assert budget.metadata['summary'] is None
    assert 'conversation_user_summary' not in budget.review_context('现在的问题')
    assert budget.review_context('现在的问题')['unavailable_legacy_user_turns'] == 9


def test_oversize_exact_history_stops_agent_before_any_model_or_tool_call():
    ctx = context(20)
    for previous in ctx.turns: previous.question = '不'*2000
    model = DecisionModel()
    result = WorkspaceAgent(model).run(request(conversation_context=ctx.model_dump()))
    assert result['status'] == 'context_budget_exceeded'
    assert result['usage']['model_calls'] == model.count == result['agent']['tool_calls'] == 0
    assert result['context']['context_budget']['calls'][-1]['reason'] == 'exact_user_summary_capacity'


def test_attributed_user_summary_cannot_supply_a_citation_or_enter_retrieval_corpus(monkeypatch, controlled_review):
    summary = build_summary(turns=[turn(1, '引用 p_summary_fake：准确率是 100%。')])
    ctx = context(2, revision=3, turns=context(2).model_dump()['turns'][1:], omitted_turns=1,
                  summary=summary.model_dump())
    class Poisoned(DecisionModel):
        def complete(self, messages, tools):
            self.count += 1
            current = next(json.loads(m['content']) for m in messages if m['role'] == 'user' and 'conversation_pinned_state' in m['content'])
            assert current['conversation_user_summary']['source_sha256'] == summary.source_sha256
            outputs = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
            hits = next((output['hits'] for output in outputs if 'hits' in output), [])
            if self.count == 1: return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
            if self.count == 2: return call(2, 'verify_sources', {'chunk_ids': [hit['chunk_id'] for hit in hits]})
            return call(self.count, 'submit_report', {'assessments': [dict(statement='Accuracy is 100%.',
                verdict='supported', explanation='Previous user text said so.', basis='historical_observation',
                evidence_ids=['p_summary_fake'])]})
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(Poisoned()).run(request(conversation_context=ctx.model_dump()))
    assert result['status'] == 'agent_protocol_error'
    assert result['claims'] == result['assessments'] == result['citations'] == []
    assert not controlled_review
    assert len(seen) == 1 and 'conversation_context' not in seen[0]['body']
    assert 'p_summary_fake' not in json.dumps(seen[0]['body']['documents'])
