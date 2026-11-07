"""Protect frozen metric denominators and pre-dispatch spending reservations."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('round15_harness',
    Path(__file__).parents[1]/'scripts/evaluate_persistent_index_round15.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def question():
    return {'id': 'q', 'relevant_doc_ids': ['a', 'b'],
            'contradictory_pair': ['a', 'b'], 'expected_evidence_status': 'contradictory'}


def usage(requests=1, inputs=1, tokens=2, unknown=False):
    return {'usage': {'embedding_requests': requests, 'embedded_inputs': inputs,
                     'embedding_tokens': tokens, 'provider_usage_unknown': unknown,
                     'model_calls': 0}}


def test_retrieval_scores_nested_top_three_without_duplicate_inflation():
    result = {'retrieval': {'hits': [{'document_id': i} for i in ['a', 'a', 'x', 'b']]}}
    score = harness.score_retrieval(question(), result, 'dense')
    assert score['recall_at_3'] == .5
    assert score['precision_at_3'] == pytest.approx(1/3)
    assert score['precision_at_3_ceiling'] == pytest.approx(2/3)
    assert score['contradictory_pair_present'] is False
    assert score['semantic_status'] == 'not_evaluated_extractive_only'


def test_partial_run_keeps_all_planned_denominator_and_failure_counts():
    first = harness.score_retrieval(question(), {'retrieval': {'hits': [{'document_id': 'a'}, {'document_id': 'b'}]}}, 'bm25')
    rows = [first, {'strategy': 'bm25', 'question_id': 'q2', 'status': 'failed'}]
    summary = harness.summarize_retrieval(rows, [question(), question(), question()])['bm25']
    assert summary['completed_questions'] == 1
    assert summary['failed_questions'] == 1
    assert summary['not_attempted_questions'] == 1
    assert summary['macro_recall_at_3_all_planned'] == pytest.approx(1/3)
    assert summary['macro_precision_at_3_all_planned'] == pytest.approx(2/9)
    assert summary['contradictory_pairs_present'] == 1


def test_reservation_caps_are_checked_before_adding_or_sending_work():
    budget = harness.EmbeddingBudget(0, clock=lambda: 1)
    budget.reserve('allowed', ['a']*60, 60)
    before = dict(budget.reserved)
    with pytest.raises(RuntimeError, match='Pre-dispatch'):
        budget.reserve('forbidden', ['a'], 1)
    assert budget.reserved == before
    assert len(budget.entries) == 1
    assert budget.entries[0]['status'] == 'reserved'


def test_conservative_unicode_reservation_and_wall_time_gate():
    budget = harness.EmbeddingBudget(0, clock=lambda: 1)
    entry = budget.reserve('unicode', ['证据'], 1)
    assert entry['reservation']['embedding_tokens'] == len('证据'.encode('utf-8')) + 16
    late = harness.EmbeddingBudget(0, clock=lambda: 280)
    with pytest.raises(RuntimeError, match='remaining wall budget'):
        late.reserve('late-query', ['q'], 1, minimum_seconds=25)
    assert late.entries == []


def test_unknown_attempt_keeps_reservation_and_does_not_fabricate_total_usage():
    budget = harness.EmbeddingBudget(0, clock=lambda: 1)
    entry = budget.reserve('timeout', ['q'], 1)
    budget.sent(entry)
    budget.uncertain(entry)
    report = budget.report()
    assert report['reserved_upper_bounds']['embedding_requests'] == 1
    assert report['provider_usage_unknown'] is True
    assert report['reported_total_embedding_tokens'] is None
    assert report['total_estimate_usd'] is None


def test_zero_cost_replay_does_not_release_conservative_reservation():
    budget = harness.EmbeddingBudget(0, clock=lambda: 1)
    entry = budget.reserve('replay', ['title\nbody'], 1)
    budget.sent(entry)
    budget.account(entry, usage(0, 0, 0))
    assert budget.known['embedding_tokens'] == 0
    assert budget.reserved['embedding_tokens'] > 0
    assert budget.report()['total_estimate_usd'] == 0


def test_unexpected_provider_work_is_a_failure_before_next_request():
    budget = harness.EmbeddingBudget(0, clock=lambda: 1)
    entry = budget.reserve('free-only')
    budget.sent(entry)
    with pytest.raises(RuntimeError, match='exceeded pre-call reservation'):
        budget.account(entry, usage())
    assert entry['status'] == 'budget_mismatch'
