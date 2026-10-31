"""Explicit coverage slots, not general semantic-completeness certification."""
import copy
import json
from pathlib import Path

import pytest

from lablens.answer_coverage import overall_requirements, prepare_review_draft
from lablens.answer_quality import make_review_contract, check_review_scope
from lablens.assessment import resolve_selections, selection_schema
from lablens.rag import _generation_payload
from test_answer_quality import assessment
from test_workspace_agent import DecisionModel, call, report, request, retrieval_server
from lablens.workspace_agent import WorkspaceAgent


def question(turn):
    data = json.loads(Path('docs/evaluations/conversation-round5/development/inputs/scenarios.json').read_text())
    return next(t['question'] for s in data['scenarios'] for t in s['turns'] if t['id'] == turn)


@pytest.mark.parametrize(('turn', 'subjects'), [('cedar-t1', ['Dune', 'Reed']), ('harbor-t1', ['Quill', 'Sable'])])
def test_real_user_requests_reserve_both_overall_questions(turn, subjects):
    original = assessment()
    before = copy.deepcopy(original)
    prepared, meta = prepare_review_draft(question(turn), original)
    assert original == before
    assert [r['subject'] for r in overall_requirements(question(turn))] == subjects
    assert len(meta['added_review_slots']) == 2
    assert meta['semantic_coverage_verified'] is False
    for row in prepared['assessments'][1:]:
        assert row['verdict'] == 'insufficient'
        assert row['quotes'] == row['citation_ids'] == []
        assert '不是资料缺失' in row['explanation']


@pytest.mark.parametrize('q', [
    '只比较 Dune 和 Reed 的分项，不要总体结论。',
    'Compare Dune and Reed, only individual metrics, no overall verdict.',
    'Latency?', 'What is the overall verdict?',
    'Compare Dune and Reed and Birch and Pine, give overall verdicts.',
    'Compare Dune and Dune, give overall verdicts.',
    'approved rule 与 signed table 的总体结论。',
    '比较 Dune 和 Reed。更正，只给 Dune 的本次验收结论。',
    'Compare Dune and Reed. Correction: only give Reed an overall verdict.',
    'Compare Latency and Throughput and give overall verdict.',
    '比较 Dune 和 Reed。更正，本轮只看 Dune，不是 Reed，给 Dune 的本次验收结论。',
    'Compare Dune and Reed. Correction: only Dune, not Reed, needs an overall verdict.',
])
def test_no_slots_for_unsupported_or_explicitly_excluded_requests(q):
    assert overall_requirements(q) == []


def test_existing_overall_rows_not_duplicated_and_names_not_substrings():
    draft = assessment(statement='Dune 的总体结论')
    draft['assessments'].append(assessment(statement='Reedwood 的总体结论')['assessments'][0])
    prepared, meta = prepare_review_draft(question('cedar-t1'), draft)
    assert meta['added_review_slots'] == ['Reed 的本次验收总判定。']
    assert len(prepared['assessments']) == 3


def test_repeated_name_without_narrowing_does_not_erase_explicit_comparison():
    assert len(overall_requirements('比较 Dune 和 Reed，给完整验收结论。Dune 的温度要单独解释。')) == 2


def test_overfull_draft_fails_without_dropping_metrics():
    draft = {'assessments': [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(7)]}
    with pytest.raises(ValueError, match='eight-assessment'):
        prepare_review_draft(question('cedar-t1'), draft)
    assert len(draft['assessments']) == 7


def test_schema_and_review_keep_original_metrics_and_new_slots_without_arbitrary_expansion():
    draft = {'assessments': [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(4)]}
    prepared, coverage = prepare_review_draft(question('cedar-t1'), draft)
    contract = make_review_contract(question('cedar-t1'), prepared)
    contract['explicit_coverage_slots'] = coverage
    payload = _generation_payload(question('cedar-t1'), [], {'model': 'controlled'}, draft=prepared,
                                  context_data={'scoped_review_contract': contract})
    schema = payload['response_format']['json_schema']['schema']
    assert schema['properties']['assessments']['minItems'] == schema['properties']['assessments']['maxItems'] == 6
    assert 'AND/OR' in payload['messages'][0]['content']
    assert 'not a finding that evidence is missing' in payload['messages'][0]['content']
    invalid = copy.deepcopy(prepared)
    invalid['assessments'].pop()
    with pytest.raises(ValueError, match='original proposition'):
        check_review_scope(question('cedar-t1'), prepared, invalid)


def test_eight_rows_allowed_but_unknown_evidence_still_rejected():
    assert selection_schema([])['properties']['assessments']['maxItems'] == 8
    with pytest.raises(ValueError, match='evidence span'):
        resolve_selections({'assessments': [dict(statement='Overall verdict', verdict='supported',
            explanation='Pass', basis='documented_fact', evidence_ids=['fabricated'])]}, [])


def test_agent_supplies_slots_to_single_existing_review(monkeypatch, controlled_review):
    # The controlled reviewer only echoes placeholders; this is wiring evidence,
    # not proof a real model can infer the acceptance verdict.
    req = request(question='Compare Dune and Reed and give overall verdicts.')
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(DecisionModel()).run(req)
    assert len(controlled_review) == 1
    raw = controlled_review[0]['raw']
    assert len(raw['assessments']) == 3
    event = next(e for e in result['agent']['trace'] if e['event'] == 'review_coverage_slots')
    assert len(event['output']['added_review_slots']) == 2
    assert result['usage']['model_calls'] == 4  # unchanged 3 scripted decisions + review


# Re-export the existing fixture for this integration test module.
from test_workspace_agent import controlled_review  # noqa: E402,F401
