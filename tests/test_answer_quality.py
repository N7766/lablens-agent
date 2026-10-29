"""Controlled guard regressions, not a semantic/model accuracy benchmark."""
import copy
import json
from pathlib import Path

import pytest

from lablens import rag
from lablens.answer_quality import (answer_contract, selected_evidence_findings,
    make_review_contract, check_review_scope, enforce_review_checks)
from lablens.assessment import evidence_catalog
from lablens.workspace_agent import WorkspaceAgent
from test_conversation import context
from test_workspace_agent import request, call


def assessment(prose='Observed latency is 96 ms.', quotes=('latency: 96 ms',), **changes):
    item = dict(statement='Observed latency', verdict='supported', explanation=prose,
                basis='documented_fact', citation_ids=['c'],
                quotes=[dict(chunk_id='c', quote=quote) for quote in quotes])
    item.update(changes)
    return {'assessments': [item]}


@pytest.mark.parametrize(('prose', 'quotes'), [
    ('96 毫秒高于 80 毫秒门槛。', ('latency 96 ms',)),
    ('The east latency is 138 ms.', ('overall latency is 162 ms; west is 171 ms',)),
    ('The result is 96 - 80 = 17 ms.', ('96 ms observed', '80 ms threshold')),
    ('Failure ratio is 1/600 = 0.27%.', ('600 jobs, 1 failure',)),
    ('The result is 96 - 50 = 46 ms.', ('96 ms observed', '80 ms threshold')),
    ('The source does not establish causation, but measured latency is 100 ms.', ('latency is 96 ms',)),
    ('Measured latency is 0 ms.', ('latency is 1,000 ms',)),
    ('Measured latency is 1,000 ms.', ('latency is 0 ms',)),
    ('Failure ratio: 1/600 = 0.17%.', ('Totals: 600. Counter value: 1.23.',)),
    ('Failure ratio: 1/600 = 0.17%.', ('Totals: 600. Counter value: 1,000.',)),
])
def test_missing_local_numeric_anchor_or_bad_arithmetic_is_detected(prose, quotes):
    findings = selected_evidence_findings(assessment(prose, quotes))
    assert len(findings) == 1 and findings[0]['rule'] == 'quantity_not_anchored_in_selected_quotes'


@pytest.mark.parametrize(('prose', 'quotes'), [
    ('96 毫秒高于 80 毫秒门槛。', ('latency 96 ms', 'limit is 80 ms')),
    ('96 - 80 = 16 ms above the limit.', ('96 ms observed', '80 ms threshold')),
    ('Compared with 80 ms, 96 ms is 16 ms slower.', ('96 ms observed', '80 ms threshold')),
    ('96 毫秒比 80 毫秒高 16 毫秒。', ('96 ms observed', '80 ms threshold')),
    ('Failure ratio: 1/600 = 0.17%.', ('600 jobs, 1 failure',)),
    ('Failure ratio: 2 ÷ 600 ≈ 0.33 percent.', ('600 jobs, 2 failures',)),
    ('Observed latency was 0.096 seconds.', ('observed 96 ms',)),
    ('Difference is 0.096 s - 80 ms = 16 ms.', ('observed 96 ms', 'limit 80 ms')),
    ('Observed offset was -2.5 ms.', ('offset -2.5 milliseconds',)),
    ('Observed latency is 1e3 ms.', ('latency 1 second',)),
    ('不是 80 毫秒，而是 96 毫秒。', ('latency 96 ms',)),
    ('It is not 80 ms; it is 96 ms.', ('latency 96 ms',)),
    ('p95 for job-138 on 2026-10-29 is documented.', ('this local run was recorded',)),
    ('Measured latency is 1,000 ms.', ('latency is 1000 ms',)),
    ('Measured latency is 1000 ms.', ('latency is 1,000 ms',)),
    ('Run A, 80 ms; run B, 96 ms.', ('80 ms and 96 ms',)),
    ('Failure ratio: 1/600 = 0.17%.', ('Total jobs: 600. Failures: 1.',)),
    ('Failure ratio: 1/600 = 0.17%.', ('Total jobs: 600, failures: 1, no other events.',)),
])
def test_valid_anchors_conversions_arithmetic_and_nonmetric_numbers_pass(prose, quotes):
    assert selected_evidence_findings(assessment(prose, quotes)) == []


def test_statement_cannot_hide_unanchored_quantity_behind_generic_explanation():
    value = assessment('See the selected report.', statement='Latency is 138 ms.', quotes=('overall 162 ms',))
    assert selected_evidence_findings(value)[0]['quantities'] == ['138 ms']


@pytest.mark.parametrize('verdict', ['contradicted', 'insufficient'])
def test_hypothetical_or_false_proposition_is_not_a_positive_numeric_claim(verdict):
    value = assessment('Cannot confirm 80 ms; no measurement was made.', (), verdict=verdict,
                       statement='The latency is 80 ms.', citation_ids=[])
    assert selected_evidence_findings(value) == []


@pytest.mark.parametrize('quantity', ['1e9999999 ms', '1e-9999999 ms', '9' * 500 + ' ms'])
def test_untrusted_huge_quantities_are_bounded_without_overflow(quantity):
    findings = selected_evidence_findings(assessment(quantity, (quantity,)))
    assert findings[0]['rule'] == 'quantity_outside_bounded_parser'


def test_large_close_integers_cannot_collide_after_decimal_scaling():
    value = assessment('Latency is 123456789012345678901234567890 ms.', ('123456789012345678901234567891 ms',))
    assert selected_evidence_findings(value)[0]['rule'] == 'quantity_outside_bounded_parser'


def test_numeric_anchor_does_not_certify_subject_direction_or_entailment():
    # The checker deliberately cannot tell that the metric belongs to another
    # machine or that "faster" has the wrong direction. The evaluation must.
    wrong = assessment('Node-B has 96 ms versus 80 ms, which is 16 ms faster.', ('Node-A 96 ms; limit 80 ms',))
    assert selected_evidence_findings(wrong) == []
    _, quality = enforce_review_checks('latency?', wrong, wrong)
    assert quality['semantic_entailment_verified'] is False


def test_guard_withholds_prose_instead_of_attaching_number_matching_source():
    value = assessment('96 ms exceeds 80 ms.', ('observed 96 ms',))
    protected, quality = enforce_review_checks('latency?', value, value)
    item = protected['assessments'][0]
    assert item['verdict'] == 'insufficient' and '96 ms exceeds' not in item['explanation']
    assert item['quotes'] == value['assessments'][0]['quotes']
    assert item['citation_ids'] == ['c'] and quality['checked_assessments'] == 1
    assert value['assessments'][0]['verdict'] == 'supported'  # immutable input


def test_review_cannot_add_an_unrequested_assessment_even_if_its_quotes_exist():
    draft = assessment('Overall records disagree.', verdict='insufficient')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'].append(assessment('Node-east data is invalid without authority.',
        statement='Subgroup validity', verdict='insufficient')['assessments'][0])
    with pytest.raises(ValueError, match='original proposition'):
        check_review_scope('Overall result?', draft, reviewed)


def test_review_cannot_hide_new_subject_validity_refusal_inside_existing_row():
    draft = assessment('Overall records disagree.', verdict='insufficient')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'][0]['explanation'] = 'Node-east measured 138 ms, but no authority confirms its validity.'
    protected, quality = enforce_review_checks('Overall result?', draft, reviewed)
    assert quality['findings'][0]['rule'] == 'insufficient_review_introduced_named_subject'
    assert quality['findings'][0]['identifiers'] == ['Node-east']
    assert 'no authority' not in protected['assessments'][0]['explanation']


@pytest.mark.parametrize('prose', ['无法确认 gpu-b 功耗。', 'gpu-b 没有可核对的功耗记录。', 'gpu-b has no measurement of power.'])
def test_new_subject_refusal_does_not_require_authority_keyword(prose):
    draft = assessment('现有资料未提供功耗。', verdict='insufficient')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'][0]['explanation'] = prose
    assert check_review_scope('功耗是多少？', draft, reviewed)[0]['identifiers'] == ['gpu-b']


def test_review_can_correct_verdict_and_add_named_source_provenance_for_valid_unknown():
    draft = assessment('The setting proves historical behavior.', statement='Historical behavior')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'][0].update(verdict='insufficient', basis='historical_observation',
        explanation='According to Run-log, configuration alone cannot establish historical execution or authority.')
    assert check_review_scope('What happened historically?', draft, reviewed) == []
    protected, _ = enforce_review_checks('What happened historically?', draft, reviewed)
    assert protected['assessments'][0]['explanation'] == reviewed['assessments'][0]['explanation']


@pytest.mark.parametrize('prose', ['gpu-a 的有效性不能确认，缺少 run-2026 的观测。',
    '引用 run-2026 的记录不足以判断 gpu-a 的有效性。'])
def test_named_missing_run_observation_is_not_automatically_new_refusal_subject(prose):
    draft = assessment('gpu-a 功耗未知。', verdict='insufficient')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'][0]['explanation'] = prose
    assert check_review_scope('gpu-a 功耗？', draft, reviewed) == []


@pytest.mark.parametrize('prose', ['Cannot confirm long-term behavior without additional observations.',
    'Cannot assess out-of-sample performance from these data.'])
def test_hyphenated_adjective_is_not_a_named_refusal_subject(prose):
    draft = assessment('The available configuration alone is insufficient.', verdict='insufficient')
    reviewed = copy.deepcopy(draft)
    reviewed['assessments'][0]['explanation'] = prose
    assert check_review_scope('Historical behavior?', draft, reviewed) == []


def test_review_contract_does_not_claim_draft_question_coverage():
    contract = make_review_contract('Compare both populations and metrics.', assessment())
    assert contract['draft_coverage_verified'] is False
    payload = rag._generation_payload('Compare both.', [], {'model': 'controlled'},
        draft=assessment(), context_data={'scoped_review_contract': contract})
    schema = payload['response_format']['json_schema']['schema']
    assert schema['$defs']['AssessmentSelection']['properties']['statement']['enum'] == ['Observed latency']
    assert schema['properties']['assessments']['minItems'] == schema['properties']['assessments']['maxItems'] == 1
    data = json.loads(payload['messages'][1]['content'])
    assert data['server_review_contract'] == {key: value for key, value in contract.items() if key != 'statements'}
    assert 'scoped_review_contract' not in data['untrusted_conversation_context']


def test_real_review_path_applies_guard_once_without_extra_model_call(monkeypatch):
    hit = dict(chunk_id='c', document_id='d', title='Report', source='report.md', text='observed latency: 96 ms',
        start=0, end=23, content_sha256='controlled')
    draft = assessment('96 ms exceeds the 80 ms limit.', (hit['text'],))
    review = dict(assessments=[dict(statement='Observed latency', verdict='supported',
        explanation='96 ms exceeds the 80 ms limit.', basis='documented_fact', evidence_ids=list(evidence_catalog([hit])))])
    calls = []
    def provider(config, path, payload, usage, kind):
        calls.append(payload)
        return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(review)))])
    monkeypatch.setattr(rag, '_post', provider)
    result = rag.review_assessments('Does it meet the limit?', draft, [hit], {}, {'model': 'controlled'},
        context_data={'scoped_review_contract': make_review_contract('Does it meet the limit?', draft)})
    assert len(calls) == 1 and result['status'] == 'refused'
    assert result['assessment_validation']['selected_evidence_checks']['findings'][0]['quantities'] == ['80 ms']
    assert result['assessment_validation']['semantic_entailment_verified'] is False
    assert result['generation_review']['calls'] == 1


def test_noncompliant_review_row_expansion_is_withheld_not_retried(monkeypatch):
    draft = assessment('A controlled comparison is missing.', (), verdict='insufficient', citation_ids=[])
    review = {'assessments': [dict(statement='Unrequested claim', verdict='insufficient',
        explanation='Missing.', basis='causal_claim', evidence_ids=[])]}
    calls = []
    def provider(*args):
        calls.append(args)
        return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(review)))])
    monkeypatch.setattr(rag, '_post', provider)
    with pytest.raises(rag.RAGError, match='answer withheld'):
        rag.review_assessments('Why?', draft, [], {}, {'model': 'controlled'},
            context_data={'scoped_review_contract': make_review_contract('Why?', draft)})
    assert len(calls) == 1


def paired_context(previous='Compare Node-east and Node-west.', **overrides):
    ctx = context(1, **overrides)
    ctx.turns[-1].question = previous
    return ctx


@pytest.mark.parametrize(('previous', 'current', 'choices'), [
    ('Compare Node-east and Node-west.', 'Did that machine pass?', ['Node-east', 'Node-west']),
    ('把 Node-east 和 Node-west 放在一起看。', '那台机器的延迟过关了吗？', ['Node-east', 'Node-west']),
    ('比较“节点甲”和“节点乙”。', '它是否达标？', ['节点甲', '节点乙']),
])
def test_parallel_latest_user_targets_force_singular_reference_clarification(previous, current, choices):
    contract = answer_contract(current, paired_context(previous))
    assert contract['requires_clarification'] and contract['choices'] == choices
    assert contract['draft_coverage_verified'] is False


@pytest.mark.parametrize('current', ['Do both machines pass?', 'Node-east passed?', '两台机器各自延迟是多少？'])
def test_explicit_target_or_plural_question_is_not_forced_to_clarify(current):
    assert not answer_contract(current, paired_context())['requires_clarification']


def test_assistant_answer_and_pinned_state_cannot_create_an_ambiguity():
    ctx = paired_context('What measurements are available?')
    ctx.turns[-1].answer = 'Compare Node-east and Node-west.'
    ctx.state.focus = 'Node-east and Node-west'
    assert not answer_contract('Did that machine pass?', ctx)['requires_clarification']


def test_latest_single_target_correction_clears_old_pair():
    ctx = context(2)
    ctx.turns[0].question = 'Compare Node-east and Node-west.'
    ctx.turns[1].question = 'Correction: use Node-west only.'
    assert not answer_contract('Did that machine pass?', ctx)['requires_clarification']


def test_same_user_turn_can_narrow_pair_before_singular_followup():
    ctx = paired_context('比较 Node-A 和 Node-B；最后只看 Node-B。')
    assert not answer_contract('它的延迟呢？', ctx)['requires_clarification']


def test_live_agent_contract_forces_model_clarification_with_zero_tools_or_review(monkeypatch):
    class Clarifier:
        def complete(self, messages, tools):
            assert [tool['function']['name'] for tool in tools] == ['request_clarification']
            return call(1, 'request_clarification', {'question': 'Node-east or Node-west?',
                'choices': ['Node-east', 'Node-west']})
    monkeypatch.setattr(rag, 'review_assessments', lambda *a, **k: pytest.fail('Unexpected review'))
    result = WorkspaceAgent(Clarifier()).run(request(question='Did that machine pass?',
        conversation_context=paired_context().model_dump()))
    assert result['status'] == 'clarification_required'
    assert result['usage']['model_calls'] == 1 and result['agent']['tool_calls'] == 1
    assert result['retrieval']['searches'] == [] and result['assessments'] == []
    assert result['agent']['trace'][0]['output']['requires_clarification'] is True


def test_native_model_cannot_bypass_forced_clarification_by_searching():
    class Bypass:
        def complete(self, *_):
            return call(1, 'search_workspace', dict(query='accuracy', strategy='bm25', top_k=3))
    result = WorkspaceAgent(Bypass()).run(request(question='Did that machine pass?',
        conversation_context=paired_context().model_dump()))
    assert result['status'] == 'agent_protocol_error' and result['agent']['tool_calls'] == 0
    assert result['usage']['generation_tokens']['total_tokens'] == 5


def test_actual_exposed_old_failures_trigger_selected_span_and_review_scope_checks():
    root = Path(__file__).resolve().parents[1] / 'docs/evaluations/conversation-round2/new-scenarios/run'
    def saved(group, case):
        return json.loads((root / group / case / 'task-final.json').read_text())['result']
    corrected_target = saved('larch', 'larch-t2')
    findings = selected_evidence_findings(corrected_target)
    assert {finding['assessment_index'] for finding in findings} >= {0, 1}
    wrong_span = saved('cedar', 'cedar-t3')
    assert '138毫秒' in ''.join(selected_evidence_findings(wrong_span)[0]['quantities'])
    refusal = saved('cedar', 'cedar-t1')
    draft = next(event['arguments'] for event in refusal['agent']['trace']
                 if event['event'] == 'tool_started' and event.get('name') == 'verify_and_submit_report')
    assert len(draft['assessments']) == 1 and len(refusal['assessments']) == 2
    with pytest.raises(ValueError):
        check_review_scope(refusal['question'], draft, refusal)
