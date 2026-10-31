"""Action-policy controls, not proof that a real model produces a good report."""
import json
from pathlib import Path

import pytest

from lablens.clarification_policy import clarification_policy
from lablens.evidence_scope import user_reference_contract


@pytest.mark.parametrize('question', [
    '请逐项给结论和还缺的最小证据。',
    '请给出结论，并说明缺少什么证据。',
    '请分别更新判断和所需的信息。',
    '给出当前判断，列出仍需要的必要资料。',
    'Give conclusions and list the missing evidence.',
    'Provide an assessment and the evidence still needed.',
    'What can we conclude and what evidence is missing?',
    'State a verdict and list required information.',
    'Update the conclusion and name any additional records.',
])
def test_explicit_bounded_report_request_keeps_model_on_report_or_search(question):
    policy = clarification_policy(question, lookup_count=1)
    assert policy['allow_clarification'] is False
    assert policy['rule'] == 'explicit_conclusion_and_evidence_gap_after_retrieval'
    assert policy['semantic_ambiguity_verified'] is False
    assert policy['evidence_sufficiency_verified'] is False
    assert policy['explicit_bounded_answer_request'] is True


@pytest.mark.parametrize('question', [
    'Which one? Give conclusions and list missing evidence.',
    '请对它逐项给结论和还缺的最小证据。',
    'Give conclusions and missing evidence for the target I forgot to name.',
])
def test_first_decision_can_clarify_genuinely_missing_scope(question):
    policy = clarification_policy(question, lookup_count=0)
    assert policy['allow_clarification']
    assert policy['rule'] == 'initial_user_scope_clarification_available'


def test_narrow_unresolved_reference_guard_has_priority_even_after_lookup():
    question = 'Compare Dune and Reed. Did it pass? Give conclusions and missing evidence.'
    contract = user_reference_contract(question)
    assert contract['requires_clarification']
    policy = clarification_policy(question, lookup_count=1,
        requires_clarification=contract['requires_clarification'])
    assert policy['allow_clarification']
    assert policy['rule'] == 'unresolved_user_reference_takes_priority'


@pytest.mark.parametrize('question', [
    'Give conclusions and missing evidence, but first ask me which deployment I mean.',
    '先澄清对象，然后给出结论和缺失的证据。',
    'Please clarify the target before you give conclusions and missing evidence.',
    '给出结论和还缺的证据前，先确认目标。',
    '先问清楚对象，然后给结论和缺失的证据。',
    'Before giving conclusions and missing evidence, ask me which deployment.',
])
def test_explicit_request_to_clarify_first_is_preserved(question):
    policy = clarification_policy(question, lookup_count=1)
    assert policy['allow_clarification']
    assert policy['rule'] == 'explicit_clarify_first_request'


@pytest.mark.parametrize('question', [
    '不要给结论和缺失的证据，先解释用词。',
    "Do not give conclusions or missing evidence yet.",
    "Do not yet give conclusions and missing evidence.",
    "不需要你给出结论和缺失的证据。",
    "Don't provide an assessment with missing information.",
])
def test_negated_report_request_does_not_force_reporting(question):
    policy = clarification_policy(question, lookup_count=1)
    assert policy['allow_clarification']
    assert policy['rule'] == 'conclusion_request_negated'


@pytest.mark.parametrize('question', [
    'Tell me more.', 'Which run failed?', '请给结论。', '还缺什么证据？',
    'Do you have the missing evidence?', 'What is a conclusion?',
    'What does the configuration say?', '它是否通过验收？',
    'Provide the record that is missing.',
])
def test_ordinary_requests_do_not_lose_clarification(question):
    assert clarification_policy(question, lookup_count=2)['allow_clarification']


def test_zero_hits_still_permits_bounded_report_without_claiming_nonoccurrence():
    # The function intentionally has no hit-count or source-text input. A
    # no_hits response is a completed lookup, but says nothing about history.
    policy = clarification_policy('Give conclusions and missing evidence.', lookup_count=1)
    assert not policy['allow_clarification']
    assert policy['evidence_sufficiency_verified'] is False
    assert not {'answer', 'verdict', 'assessments', 'citation_ids'}.intersection(policy)


def test_actual_sealed_kl_t2_clarification_would_not_be_offered_after_its_search():
    root = Path(__file__).resolve().parents[1]
    path = root / 'docs/evaluations/conversation-round6/runs/round6-once/kl-t2-final-task.json'
    sealed = path.read_bytes()
    task = json.loads(sealed)
    result = task['result']
    trace = result['agent']['trace']
    completed = [e for e in trace if e['event'] == 'tool' and e.get('name') == 'search_workspace']
    assert len(completed) == 1
    assert {h['document_id'] for h in result['retrieval']['hits']} == {'kl-run', 'kl-retention', 'kl-alert'}
    assert result['status'] == 'clarification_required'
    assert result['assessments'] == []
    required = next(e['output']['requires_clarification'] for e in trace if e['event'] == 'answer_contract')
    policy = clarification_policy(task['request']['question'], lookup_count=len(completed),
                                  requires_clarification=required)
    assert not policy['allow_clarification']
    assert path.read_bytes() == sealed  # An offline policy replay never changes the recorded outcome.


@pytest.mark.parametrize('count', [-1, 1.0, '1', True, None])
def test_invalid_lookup_state_cannot_silently_disable_clarification(count):
    with pytest.raises(ValueError):
        clarification_policy('Give conclusions and missing evidence.', lookup_count=count)


@pytest.mark.parametrize('required', [1, 'false', None])
def test_unvalidated_obligation_is_rejected(required):
    with pytest.raises(TypeError):
        clarification_policy('Give conclusions and missing evidence.', lookup_count=1,
                             requires_clarification=required)


def test_nontext_question_is_rejected():
    with pytest.raises(TypeError):
        clarification_policy(None, lookup_count=0)


@pytest.mark.parametrize('question', [
    'Give conclusions and missing evidence, but ask me which deployment if the records identify more than one.',
    '请给出结论和缺失的证据，若记录对应多个部署就向我确认对象。',
    'Give conclusions and missing evidence. If records conflict, clarify which version applies.',
])
def test_conditional_user_scope_clarification_request_is_preserved(question):
    result = clarification_policy(question, lookup_count=1)
    assert result['allow_clarification'] is True
    assert result['rule'] == 'explicit_scope_clarification_request'
