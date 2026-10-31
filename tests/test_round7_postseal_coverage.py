"""Read sealed drafts only; post-seal engineering replay is not new inference."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from lablens.answer_coverage import CoverageContractError, prepare_review_draft
from lablens.assessment import resolve_selections
from test_answer_quality import assessment


QUESTION = 'Compare Birch and Maple and give overall verdicts.'


def replay(round_number, turn):
    path = Path(f'docs/evaluations/conversation-round{round_number}/runs/round{round_number}-once/{turn}-final-task.json')
    before = path.read_bytes()
    task = json.loads(before)
    result = task.get('result') or task['error_result']
    trace = result['agent']['trace']
    submit = next(e for e in trace if e['event'] == 'tool_started' and e.get('name') == 'verify_and_submit_report')
    retrieved = next(e['output']['hits'] for e in trace if e['event'] == 'tool' and e.get('name') == 'search_workspace')
    draft = resolve_selections(submit['arguments'], retrieved)
    original = copy.deepcopy(draft)
    prepared, meta = prepare_review_draft(task['request']['question'], draft)
    assert draft == original and path.read_bytes() == before
    assert prepared == draft
    assert meta['added_review_slots'] == []
    assert meta['semantic_coverage_verified'] is False
    assert meta['version'] == 'explicit-request-slots-v3'
    return task, prepared, hashlib.sha256(before).hexdigest()


def test_round7_cedar_existing_trial_id_verdicts_stay_eight_without_extra_slots():
    task, draft, _ = replay(7, 'cedar-t1')
    assert len(draft['assessments']) == 8
    assert [r['statement'] for r in draft['assessments'][-2:]] == ['Dune未通过C-21验收', 'Reed通过C-21验收']
    assert task['status'] == 'FAILED'  # Original live result remains a failure.


def test_round7_harbor_combined_also_predicate_stays_five_without_extra_slot():
    task, draft, _ = replay(7, 'harbor-t1')
    assert len(draft['assessments']) == 5
    assert draft['assessments'][3]['statement'] == 'Quill 不通过验收，Sable 也不通过验收'
    assert task['result']['assessments'][5]['statement'] == 'Sable 的本次验收总判定。'
    assert task['result']['assessments'][5]['explanation'].startswith('Quill')


def test_round6_harbor_existing_direct_verdict_fix_is_preserved():
    task, draft, _ = replay(6, 'harbor-t1')
    assert len(draft['assessments']) == 8 and task['status'] == 'FAILED'


@pytest.mark.parametrize('statement', [
    'Birch通过T-35验收', 'Birch未通过T-35验收', 'Birch 通过 TEST12 验收。',
    'Birch 通过 AB_2028 试验验收。', 'Birch也不通过验收', 'Birch均通过验收。',
    'Birch都未通过验收。', 'Birch 也已经通过 T-35 验收。',
    'Birch 因时延超标而也未通过 T-35 验收。',
    'Maple 不通过验收，Birch 也不通过验收。',
    'Birch 也满足全部验收条件。',
    'Birch 在 TEST12 试验中也未通过验收。',
])
def test_bounded_trial_ids_and_adverbs_do_not_duplicate_whole_candidate_propositions(statement):
    draft = assessment(statement=statement)
    before = copy.deepcopy(draft)
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert 'Birch 的本次验收总判定。' not in meta['added_review_slots']
    assert prepared['assessments'][0] == before['assessments'][0] and draft == before


@pytest.mark.parametrize('statement', [
    'Birch 的 median latency 通过 T-35 验收',
    'Birch latency passes the T-35 acceptance test.',
    'Birch 通过 T-35 延迟验收',
    'Birch 通过 T-35 验收条件 1。',
    'Birch 也通过验收条件 1。',
    'Birch 也通过验收（延迟指标）。',
    'Birch 也满足验收要求（1）。',
    'Birch 通过 p99 验收',
    'Birch 未通过 99% 验收',
    'Birch 通过 T-35 验收吗？',
    'Birch 也通过验收？',
    'Birch 是否通过 T-35 验收。',
    'Birch 能否通过 T-35 验收。',
    '如果温度合格，Birch通过T-35验收。',
    'Birch通过T-35验收，如果所有条件成立。',
    '假设 Birch 也不通过验收。',
    '若 Birch 也通过验收，则可以签字。',
    'If the condition holds, Birch passes acceptance.',
    'Birch passes acceptance?',
    'Whether Birch passes acceptance is unknown.',
])
def test_metric_scopes_conditionals_and_questions_do_not_establish_an_overall_slot(statement):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=statement))
    assert 'Birch 的本次验收总判定。' in meta['added_review_slots']


def test_genuinely_full_draft_missing_overall_still_stops_without_truncation():
    draft = {'assessments': [assessment(statement=f'Birch metric {i}')['assessments'][0] for i in range(8)]}
    before = copy.deepcopy(draft)
    with pytest.raises(CoverageContractError) as error:
        prepare_review_draft(QUESTION, draft)
    assert error.value.assessment_count == 8
    assert error.value.missing_review_slots == ['Birch 的本次验收总判定。', 'Maple 的本次验收总判定。']
    assert before == draft


@pytest.mark.parametrize('label', ['P99', 'P95', 'P50', 'P100', 'p99', 'p95', 'P99.9', 'p99.9'])
def test_percentile_labels_never_become_trial_ids_by_capitalization(label):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=f'Birch通过{label}验收。'))
    assert meta['added_review_slots'] == ['Birch 的本次验收总判定。', 'Maple 的本次验收总判定。']


@pytest.mark.parametrize('statement', [
    'Birch通过验收，Maple是否通过验收？',
    'Maple是否通过验收？Birch通过验收。',
    'Maple是否通过验收，Birch通过验收。',
    'Birch通过T-35验收，若干资料已归档。',
])
def test_other_candidate_question_or_some_records_do_not_erase_supported_slot(statement):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=statement))
    assert meta['added_review_slots'] == ['Maple 的本次验收总判定。']


def test_seven_rows_with_one_candidate_question_fit_exactly_one_slot():
    draft = {'assessments': [assessment(statement='Birch通过验收，Maple是否通过验收？')['assessments'][0]] +
             [assessment(statement=f'Metric {i}')['assessments'][0] for i in range(6)]}
    before = copy.deepcopy(draft)
    prepared, meta = prepare_review_draft(QUESTION, draft)
    assert len(prepared['assessments']) == 8 and draft == before
    assert meta['added_review_slots'] == ['Maple 的本次验收总判定。']


@pytest.mark.parametrize('statement', [
    '如果条件成立，Birch通过验收，Maple也通过验收。',
    '若温度合格，Birch通过验收，Maple也通过验收。',
    'Maple通过验收，如果温度合格，Birch通过验收。',
    'Maple通过验收；如果条件成立，Birch通过验收。',
    'Maple通过验收，Birch通过验收，如果全部条件成立。',
])
def test_candidate_preceding_and_following_conditions_still_withhold_its_slot(statement):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=statement))
    assert 'Birch 的本次验收总判定。' in meta['added_review_slots']


def test_other_candidate_in_cause_is_not_a_new_subject_or_its_own_verdict():
    _, meta = prepare_review_draft(QUESTION, assessment(statement='Birch 因 Maple 的故障而未通过验收。'))
    assert meta['added_review_slots'] == ['Maple 的本次验收总判定。']


@pytest.mark.parametrize('statement', [
    'Birch 与 Maple 总体均未通过验收。',
    'Birch 和 Maple 的总体结论为不通过。',
])
def test_explicit_joint_subjects_keep_both_existing_overall_propositions(statement):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=statement))
    assert meta['added_review_slots'] == []


@pytest.mark.parametrize('statement', [
    'Birch 与 Maple 总体是否通过验收？',
    '如果条件成立，Birch 和 Maple 的总体结论为通过。',
])
def test_joint_subject_question_or_condition_does_not_cover_either_candidate(statement):
    _, meta = prepare_review_draft(QUESTION, assessment(statement=statement))
    assert meta['added_review_slots'] == ['Birch 的本次验收总判定。', 'Maple 的本次验收总判定。']
