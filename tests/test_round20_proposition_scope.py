"""Offline negative replay and controlled transformations; no live model quality claim."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections, validate_assessments
from lablens.proposition_scope import inspect_proposition_scopes, require_reviewable_propositions, PropositionScopeError
from lablens.review_decisions import review_policy
from test_private_model_review_decisions import route, V3, V4
from test_workspace_actions import configured_model, response
from test_workspace_agent import request, retrieval_server
from lablens.workspace_agent import WorkspaceAgent

ROOT = Path(__file__).resolve().parents[1]
SEALED = ROOT / 'docs/evaluations/conversation-round19c-runtime/report/run/task-final.json'


def actual_failure():
    saved = json.loads(SEALED.read_text())
    result = saved['result']
    hits = result['retrieval']['hits']
    selected = next(e['arguments'] for e in result['agent']['trace']
                    if e.get('name') == 'verify_and_submit_report' and e['event'] == 'tool')
    draft = resolve_selections(selected, hits)
    question = saved['request']['question']
    contract = make_review_contract(question, draft)
    data = SimpleNamespace(question=question, hits=hits, draft=draft, contract=contract,
                           context={'scoped_review_contract': contract})
    return data, result['generation_review']['decisions']['reviewed_response']


def row(statement='配置启用了缓存。', verdict='insufficient', basis='current_configuration',
        explanation='配置文件写明 true，但缺少对应历史运行日志。'):
    return dict(statement=statement, verdict=verdict, basis=basis, explanation=explanation,
                citation_ids=[], quotes=[])


@pytest.mark.parametrize('protocol', [V3, V4])
def test_actual_negative_replay_stops_with_original_fields_and_usage_no_retry(monkeypatch, protocol):
    data, reviewed = actual_failure()
    original = copy.deepcopy(data.draft)
    assert 'a2' not in review_policy(data.draft, data.hits, data.contract)['retainable_assessment_ids']
    if protocol == V3:
        reviewed = dict(retained_assessment_ids=['a1'], assessments=[dict(assessment_id='a2',
            **{k: v for k, v in reviewed['decisions']['a2'].items() if k != 'action'})])
    probe = route(monkeypatch, data, reviewed, protocol=protocol)
    with pytest.raises(rag.RAGError) as error:
        probe.run()
    assert error.value.status == 'invalid_proposition_scope'
    detail = error.value.review_diagnostic
    assert detail['stage'] == 'review_result'
    assert detail['normalized_draft'] == original
    assert detail['normalized_review'] == reviewed
    assert detail['resolved_review']['assessments'][1]['statement'] == '配置启用了缓存。'
    assert len(probe.sent) == probe.usage['model_calls'] == 1
    assert probe.usage['provider_usage_unknown'] is False
    assert data.draft == original


@pytest.mark.parametrize('protocol', [V3, V4])
def test_bad_row_cannot_bypass_via_retain(monkeypatch, protocol):
    data, _ = actual_failure()
    reviewed = ({'decisions': {'a1': {'action': 'retain'}, 'a2': {'action': 'retain'}}} if protocol == V4
                else dict(retained_assessment_ids=['a1', 'a2'], assessments=[]))
    probe = route(monkeypatch, data, reviewed, protocol=protocol)
    with pytest.raises(rag.RAGError):
        probe.run()
    assert len(probe.sent) == 1


def test_existing_review_can_repair_configuration_without_claiming_question_coverage(monkeypatch):
    data, reviewed = actual_failure()
    reviewed['decisions']['a2'].update(verdict='supported',
        explanation='配置文件记录 cache_enabled 为 true；这只说明资料中的配置值，不证明历史运行实际启用。')
    probe = route(monkeypatch, data, reviewed)
    result = probe.run()
    assert len(probe.sent) == 1
    assert result['assessments'][1]['verdict'] == 'supported'
    assert '【证据支持】配置启用了缓存。' in result['answer']
    assert '【证据不足】配置启用了缓存。' not in result['answer']
    audit = result['assessment_validation']['proposition_scope']
    assert audit['rendered']['findings'] == []
    assert audit['rendered']['question_coverage_verified'] is False
    assert result['generation_review']['decisions']['semantic_correctness_verified'] is False


def test_changing_only_basis_cannot_relabel_original_configuration_as_history(monkeypatch):
    data, reviewed = actual_failure()
    reviewed['decisions']['a2']['basis'] = 'historical_observation'
    probe = route(monkeypatch, data, reviewed)
    with pytest.raises(rag.RAGError) as error:
        probe.run()
    assert any(f['rule'] == 'historical_basis_on_configuration_proposition'
               for f in error.value.review_diagnostic['proposition_scope']['findings'])


@pytest.mark.parametrize('value', [
    row(verdict='supported', explanation='配置为 true，但没有提供历史运行日志。'),
    row(verdict='contradicted', explanation='配置为 false；没有提供历史运行日志。'),
    row(explanation='未提供当前配置文件，也没有历史运行日志。'),
    row(explanation='配置值存在冲突，缺少对应运行日志。'),
    row(explanation='当前配置快照没有提供，历史运行日志也没有提供。'),
    row(statement='The runtime configuration sets cache_enabled=true.', verdict='supported', explanation='The settings file contains this value.'),
    row(statement='运行时配置记录 cache_enabled=true。', verdict='supported', explanation='配置文件记录了该值。'),
    row(statement='The current configuration enables caching.', explanation='The current configuration was not supplied; historical execution is also unknown.'),
    row(statement='历史运行启用了缓存。', basis='historical_observation', explanation='缺少历史运行日志。'),
    row(statement='配置足以证明历史运行启用了缓存。', basis='historical_observation', explanation='缺少历史运行日志。'),
])
def test_valid_configuration_and_runtime_uncertainty_are_not_blanket_rejected(value):
    assert inspect_proposition_scopes({'assessments': [value]})['findings'] == []


@pytest.mark.parametrize('value', [
    row(),
    row(explanation='配置为 true，但没有提供历史运行日志。'),
    row(explanation='缺少当前配置生效的历史运行日志。'),
    row(statement='The current configuration enables caching.', explanation='No historical execution logs were supplied.'),
    row(statement='历史运行启用了缓存。', basis='current_configuration'),
    row(basis='historical_observation'),
])
def test_explicit_scope_mismatch_is_a_validation_finding(value):
    assert inspect_proposition_scopes({'assessments': [value]})['findings']


def test_unclassified_language_does_not_turn_into_a_semantic_certificate():
    audit = inspect_proposition_scopes({'assessments': [row(statement='它当时就用了。')]})
    assert audit['rows'][0]['statement_scope'] == 'unclassified'
    assert audit['semantic_correctness_verified'] is False


def test_joint_configuration_and_runtime_proposition_cannot_be_reviewed_or_rewritten(monkeypatch):
    data, reviewed = actual_failure()
    data.draft['assessments'][1]['statement'] = '配置启用缓存，且历史运行启用了缓存。'
    data.context['scoped_review_contract'] = make_review_contract(data.question, data.draft)
    original = copy.deepcopy(data.draft)
    probe = route(monkeypatch, data, reviewed)
    with pytest.raises(rag.RAGError) as error:
        probe.run()
    assert error.value.review_diagnostic['stage'] == 'draft_contract'
    assert probe.sent == [] and data.draft == original


def test_quality_rewrite_cannot_launder_original_scope_failure(monkeypatch):
    data, reviewed = actual_failure()
    def forbidden(*args, **kwargs):
        raise AssertionError('Scope must be rejected before another guard replaces its explanation')
    monkeypatch.setattr('lablens.answer_quality.enforce_review_checks', forbidden)
    probe = route(monkeypatch, data, reviewed)
    with pytest.raises(rag.RAGError) as error:
        probe.run()
    assert error.value.status == 'invalid_proposition_scope'


def test_unscoped_review_keeps_its_existing_ability_to_split_a_mixed_draft(monkeypatch):
    data, reviewed = actual_failure()
    data.draft['assessments'][1]['statement'] = '配置启用缓存，且历史运行启用了缓存。'
    data.context = None
    cfg_ids = reviewed['decisions']['a2']['evidence_ids']
    values = [dict(statement='配置启用了缓存。', verdict='supported', basis='current_configuration',
                   explanation='配置记录 cache_enabled 为 true。', evidence_ids=cfg_ids),
              dict(statement='历史运行启用了缓存。', verdict='insufficient', basis='historical_observation',
                   explanation='没有对应历史运行日志，配置无法确认历史执行。', evidence_ids=cfg_ids)]
    probe = route(monkeypatch, data, {'assessments': values})
    result = probe.run()
    assert len(probe.sent) == 1 and result['generation_review']['payload_format'] == 'full-review-v1'
    assert [r['verdict'] for r in result['assessments']] == ['supported', 'insufficient']


def test_post_validation_audit_covers_legacy_scalar_rows_without_coverage_claim(monkeypatch):
    data, _ = actual_failure()
    data.question = '配置 cache_enabled 的值是什么，能证明历史执行吗？'
    config = next(h for h in data.hits if h['source'].endswith('config.yaml'))
    selected = dict(statement='历史运行启用了缓存。', verdict='insufficient', basis='historical_observation',
                    explanation='缺少历史运行日志。', evidence_ids=list(evidence_catalog([config])))
    data.draft = resolve_selections({'assessments': [selected]}, data.hits)
    data.context = {'scoped_review_contract': make_review_contract(data.question, data.draft)}
    probe = route(monkeypatch, data, {'decisions': {'a1': dict(action='replace',
        **{k: v for k, v in selected.items() if k != 'statement'})}})
    result = probe.run()
    audit = result['assessment_validation']['proposition_scope']
    assert len(audit['review']['rows']) == 1
    assert len(audit['rendered']['rows']) == len(result['assessments']) == 2
    assert result['assessments'][0]['statement'] == '引用配置记录 cache_enabled: true'
    assert audit['rendered']['question_coverage_verified'] is False


def test_post_validation_rendered_rows_are_checked_again(monkeypatch):
    data, reviewed = actual_failure()
    reviewed['decisions']['a2'].update(verdict='supported', explanation='配置记录 cache_enabled 为 true。')
    real = rag.validate_assessments
    def changed_render(*args, **kwargs):
        result = real(*args, **kwargs)
        result['assessments'][1].update(verdict='insufficient', explanation='缺少历史运行日志。')
        return result
    monkeypatch.setattr(rag, 'validate_assessments', changed_render)
    probe = route(monkeypatch, data, reviewed)
    with pytest.raises(rag.RAGError) as error:
        probe.run()
    assert error.value.review_diagnostic['stage'] == 'rendered_report'
    assert error.value.review_diagnostic['rendered_assessments'][1]['explanation'] == '缺少历史运行日志。'
    assert error.value.review_diagnostic['resolved_review']['assessments'][1]['verdict'] == 'supported'


@pytest.mark.parametrize('bad', [{}, {'assessments': None}, {'assessments': []},
    {'assessments': [{}]}, {'assessments': [dict(statement='x', explanation='y', verdict='insufficient')]}])
def test_unscoped_malformed_model_draft_is_controlled_and_does_not_send_review(monkeypatch, bad):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid draft must not trigger a model review')
    monkeypatch.setattr(rag, '_post', forbidden)
    usage = rag._new_usage()
    usage['model_calls'] = 1
    with pytest.raises(rag.RAGError) as error:
        rag.review_assessments('Question', bad, [], usage, {'model': 'controlled'})
    assert error.value.status == 'invalid_citations'
    assert error.value.review_diagnostic['stage'] == 'draft_contract'
    assert usage['model_calls'] == 1


@pytest.mark.parametrize('compound', [False, True])
def test_agent_scope_failure_terminates_without_publishing_or_replanning(monkeypatch, configured_model, compound):
    sent = []
    def provider(url, **kwargs):
        payload = kwargs['json']; sent.append(payload)
        data = json.loads(payload['messages'][-1]['content'])
        if payload['response_format']['json_schema']['name'] == 'evidence_review_by_id':
            assert not compound
            replacement = {k: v for k, v in data['untrusted_draft']['assessments'][0].items()
                           if k not in {'statement', 'assessment_id'}}
            return response(url, {'decisions': {'a1': dict(action='replace', **replacement)}})
        if len(sent) == 1:
            return response(url, {'decision': {'action': 'search_workspace', 'arguments': {
                'query': 'cache 缓存 配置', 'strategy': 'bm25', 'top_k': 1}}})
        outputs = [json.loads(m['content'])['untrusted_tool_result'] for m in payload['messages']
                   if m['role'] == 'user' and 'untrusted_tool_result' in m['content']]
        value = row(statement='配置启用缓存且历史运行启用了缓存。' if compound else '配置启用了缓存。')
        value.pop('quotes'); value.pop('citation_ids')
        value['evidence_ids'] = list(outputs[-1]['passages'])
        return response(url, {'decision': {'action': 'verify_and_submit_report', 'arguments': {'assessments': [value]}}})
    monkeypatch.setenv('LABLENS_REVIEW_PROTOCOL', V4)
    monkeypatch.setenv('LLM_MODEL', 'controlled-round20-model')
    monkeypatch.setattr('lablens.model.bounded_post', provider)
    monkeypatch.setattr('lablens.rag.bounded_post', provider)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        result = WorkspaceAgent(configured_model).run(request(question='配置能否证明历史运行启用了缓存？',
            documents=[dict(id='config', title='Synthetic config', source='synthetic/config.yaml', text='cache_enabled: true')]))
    assert len(sent) == result['usage']['model_calls'] == (2 if compound else 3), result
    assert len(seen) == 1
    assert result['status'] == 'agent_protocol_error'
    assert result['agent']['state'] == 'report_scope_error'
    assert not result.get('answer') and not result.get('claims')
    assert 'not a finding that evidence is missing' in result['error'] or compound
    assert not any(e['event'] == 'review_completed' for e in result['agent']['trace'])
    rejected = next(e for e in result['agent']['trace'] if e['event'] == 'tool_rejected')
    assert rejected['output'].get('normalized_arguments') or rejected['output']['validation_detail']['normalized_review']
