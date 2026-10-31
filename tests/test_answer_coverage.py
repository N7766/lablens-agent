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


def test_sealed_harbor_eight_row_draft_keeps_existing_overall_propositions():
    # Post-evaluation replay: never replace the original failed model run.
    raw = json.loads(Path('docs/evaluations/conversation-round6/runs/round6-once/harbor-t1-final-task.json').read_text())
    trace = raw['error_result']['agent']['trace']
    draft_event = trace[16]
    assert draft_event['event'] == 'tool_started' and draft_event['name'] == 'verify_and_submit_report'
    retrieved = trace[8]['output']['hits']
    resolved = resolve_selections(draft_event['arguments'], retrieved)
    before = copy.deepcopy(resolved)
    assert len(resolved['assessments']) == 8
    assert resolved['assessments'][5]['statement'] == 'Quill 因 latency 不达标而无法通过验收'
    assert resolved['assessments'][6]['statement'] == 'Sable 因成功率未达标而无法通过验收'
    prepared, coverage = prepare_review_draft(raw['request']['question'], resolved)
    assert prepared == before == resolved
    assert coverage['added_review_slots'] == []
    assert coverage['semantic_coverage_verified'] is False
    assert raw['status'] == 'FAILED'  # The recorded real-model outcome stays failed.


@pytest.mark.parametrize('statement', [
    'Quill 因 latency 不达标而无法通过验收',
    'Quill 因 latency 达标而通过验收',  # False overall conclusion must remain reviewable.
    'Quill 通过验收。',
    'Quill 未通过验收',
    'Quill 在 R-17 试验中无法通过验收',
    'Quill 在 C21 试验中无法通过验收。',
    'Quill 通过验收  。',
    'Quill 验收未通过\n',
    'Quill 验收未通过。',
    'Quill 满足全部验收条件。',
    'Quill passes acceptance.',
    'Quill fails acceptance.',
    'Quill does not pass the acceptance test.',
    'Quill meets all criteria.',
    'Quill does not meet all acceptance criteria.',
    'Quill fails acceptance because the latency is too high.',
])
def test_explicit_whole_candidate_predicates_are_not_duplicated_regardless_of_truth(statement):
    draft = assessment(statement=statement)
    before = copy.deepcopy(draft)
    prepared, coverage = prepare_review_draft(question('harbor-t1'), draft)
    assert coverage['added_review_slots'] == ['Sable 的本次验收总判定。']
    assert prepared['assessments'][0] == before['assessments'][0]
    assert draft == before


@pytest.mark.parametrize('statement', [
    'Quill 的 latency 通过验收阈值',
    'Quill latency passes acceptance.',
    'Quill median restore latency meets all criteria.',
    'Quill 的成功率已经通过验收',
    'Quill 的成功率为 99%，满足指标要求。',
    'Quill 的 latency 未达标',
    'Quill passes the latency threshold.',
    'Quill passes acceptance threshold for latency.',
    'Quill 在成功率指标中通过验收。',
    'Quill 在 latency 指标中通过验收。',
    'Quill 在成功率试验中通过验收。',  # Unsupported scope does not establish the whole-candidate slot.
    'Quill 满足验收条件 1（延迟）。',
    'Quill 满足验收条件 1。',
    'Quill 满足验收要求（1）。',
    'Quill通过验收 成功率指标。',
    'Quill通过验收\n成功率指标。',
    'Quill 验收通过 条件一。',
])
def test_per_metric_compliance_does_not_hide_missing_overall_proposition(statement):
    prepared, coverage = prepare_review_draft(question('harbor-t1'), assessment(statement=statement))
    assert coverage['added_review_slots'] == ['Quill 的本次验收总判定。', 'Sable 的本次验收总判定。']
    assert len(prepared['assessments']) == 3


def test_full_eight_row_draft_with_missing_overall_fails_explicit_contract_without_deletion():
    from lablens.answer_coverage import CoverageContractError
    draft = {'assessments': [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(8)]}
    before = copy.deepcopy(draft)
    with pytest.raises(CoverageContractError) as caught:
        prepare_review_draft(question('harbor-t1'), draft)
    assert caught.value.assessment_count == 8
    assert caught.value.missing_overall_slots == ['Quill 的本次验收总判定。', 'Sable 的本次验收总判定。']
    assert str(caught.value) == 'Required overall-verdict slots exceed the eight-assessment capacity; report withheld.'
    assert draft == before


def test_full_eight_row_draft_with_one_missing_overall_does_not_silently_skip_it():
    from lablens.answer_coverage import CoverageContractError
    draft = {'assessments': [assessment(statement='Quill 通过验收。')['assessments'][0]] +
             [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(7)]}
    with pytest.raises(CoverageContractError) as caught:
        prepare_review_draft(question('harbor-t1'), draft)
    assert caught.value.assessment_count == 8
    assert caught.value.missing_overall_slots == ['Sable 的本次验收总判定。']


def test_one_free_slot_can_hold_the_only_missing_overall_question():
    draft = {'assessments': [assessment(statement='Quill 通过验收。')['assessments'][0]] +
             [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(6)]}
    prepared, coverage = prepare_review_draft(question('harbor-t1'), draft)
    assert len(prepared['assessments']) == 8
    assert coverage['added_review_slots'] == ['Sable 的本次验收总判定。']
