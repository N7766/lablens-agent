"""Mechanical boundary regressions, not a semantic model benchmark."""
import hashlib
import json
from pathlib import Path

import pytest

from lablens.assessment import resolve_selections, validate_assessments, evidence_catalog, evidence_catalog_metadata


def sample(text='First line\nSecond line', **changes):
    hit = dict(chunk_id='c', document_id='d', title='Report', source='report.md', text=text,
               start=0, end=len(text), content_sha256='test')
    item = dict(statement='Observed result', verdict='supported', explanation='The report records this result.',
                basis='documented_fact', citation_ids=['c'], quotes=[dict(chunk_id='c', quote=text)])
    item.update(changes)
    return hit, item


def test_double_escaped_newline_can_only_repair_to_exact_source():
    hit, item = sample()
    item['quotes'][0]['quote'] = 'First line\\nSecond line'
    result = validate_assessments('question', {'assessments': [item]}, [hit])
    assert result['assessments'][0]['quotes'][0]['quote'] == hit['text']
    assert result['assessment_validation']['adjustments'][0]['rule'] == 'escaped_newline_exact_source_repair'
    item['quotes'][0]['quote'] = 'First line\\nWRONG line'
    with pytest.raises(ValueError):
        validate_assessments('question', {'assessments': [item]}, [hit])


def test_existing_literal_escape_is_not_reinterpreted():
    hit, item = sample(text='First line\\nSecond line')
    result = validate_assessments('question', {'assessments': [item]}, [hit])
    assert result['assessments'][0]['quotes'][0]['quote'] == hit['text']
    assert result['assessment_validation']['adjustments'] == []


def test_report_is_not_automatic_causal_certification():
    hit, item = sample(statement='DPO导致退化', explanation='配置是根本原因。')
    result = validate_assessments('question', {'assessments': [item]}, [hit])
    assert result['status'] == 'refused'
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessment_validation']['causal_certification'] is False


def test_supported_observation_can_explicitly_deny_causal_inference():
    hit, item = sample(explanation='报告记录准确率差异，但不能认定DPO导致退化。')
    result = validate_assessments('question', {'assessments': [item]}, [hit])
    assert result['status'] == 'answered'


def test_unknown_context_source_on_insufficient_claim_still_fails():
    hit, item = sample(verdict='insufficient', citation_ids=['unknown'], quotes=[])
    with pytest.raises(ValueError):
        validate_assessments('question', {'assessments': [item]}, [hit])


def test_runtime_inference_cannot_bypass_scope_guard_by_mislabeling_basis():
    hit, item = sample(statement='precompute_ref_log_probs=false时训练会动态重算参考概率',
                       explanation='false值会触发训练时动态计算。', basis='current_configuration')
    hit['source'] = 'src/training/utils.py:L10-L12'
    result = validate_assessments('Can historical recomputation be established?', {'assessments': [item]}, [hit])
    assert result['status'] == 'refused'
    assert result['assessment_validation']['adjustments'][0]['rule'] == 'config_code_cannot_establish_historical_or_causal_fact'


def test_insufficient_label_does_not_allow_unsupported_runtime_prose():
    hit, item = sample(text='precompute_ref_log_probs: false', verdict='insufficient',
                       statement='历史运行是否每步重算', basis='historical_observation',
                       explanation='false会触发训练时动态计算，但历史运行日志缺失。')
    hit['source'] = 'configs/run.yaml'
    result = validate_assessments('precompute_ref_log_probs: false能否证明历史每步重算？', {'assessments': [item]}, [hit])
    assert [a['verdict'] for a in result['assessments']] == ['supported', 'insufficient']
    assert '触发' not in result['answer']
    assert '配置原文' in result['assessments'][0]['explanation']


def test_directory_identity_is_not_weight_identity():
    hit, item = sample(statement='两个评估使用相同模型', explanation='model_dir字段相同。', basis='historical_observation')
    result = validate_assessments('same model?', {'assessments': [item]}, [hit])
    assert result['evidence_verdict'] == 'insufficient'
    assert '权重内容指纹' in result['answer']


@pytest.mark.parametrize('explanation', [
    '配置文件和代码逻辑表明 false 会触发每步重算，但无运行日志证明历史设置生效。',
    'The false value triggers per-step recomputation, but no historical logs are available.',
])
def test_uncited_insufficient_runtime_assertion_is_neutralized(explanation):
    hit, item = sample(statement='历史运行实际是否每步重算参考模型概率', verdict='insufficient',
                       explanation=explanation, basis='historical_observation', citation_ids=[], quotes=[])
    result = validate_assessments('Can the historical runtime be established?', {'assessments': [item]}, [hit])
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessments'][0]['explanation'] != explanation
    assert '不能据此确认具体计算机制' in result['answer']
    assert result['assessment_validation']['adjustments'][0]['rule'] == 'uncited_runtime_assertion_cannot_hide_in_insufficient'


def test_uncited_runtime_uncertainty_is_preserved():
    explanation = '缺少历史日志，不能确认 false 会触发每步重算；需要实际实现版本与运行观测。'
    hit, item = sample(statement='是否每步重算', verdict='insufficient', explanation=explanation,
                       basis='historical_observation', citation_ids=[], quotes=[])
    result = validate_assessments('Historical runtime?', {'assessments': [item]}, [hit])
    assert result['assessments'][0]['explanation'] == explanation
    assert result['assessment_validation']['adjustments'] == []


@pytest.mark.parametrize(('question', 'expected'), [
    ('配置 precompute_ref_log_probs 为何是 false？', ['precompute_ref_log_probs']),
    ('配置 r 和 precompute_ref_log_probs 的值是什么？', ['r', 'precompute_ref_log_probs']),
    ('配置 other_precompute_ref_log_probs 和 r_extra 的值是什么？', []),
])
def test_literal_scalar_selection_uses_complete_config_identifiers(question, expected):
    hit, item = sample(text='  r: 8\n  precompute_ref_log_probs: false', statement='证据需要进一步核实',
                       verdict='insufficient', explanation='当前资料不能回答。', citation_ids=[], quotes=[])
    hit['source'] = 'configs/run.yaml'
    result = validate_assessments(question, {'assessments': [item]}, [hit])
    copied = [a['statement'].removeprefix('引用配置记录 ').split(':')[0]
              for a in result['assessments'] if a['verdict'] == 'supported']
    assert copied == expected


def test_saved_development_review_keeps_one_scalar_and_no_runtime_assertion():
    """Actual saved development failure, using its original eight source docs.

    No model call and no independent evaluation data. The immutable saved model
    review is replayed so no hand-written surrogate can omit its empty-ID clause.
    """
    root = Path(__file__).resolve().parents[1]
    fixtures = json.loads((root / 'evaluation/regressions/v013/known-failures.json').read_text())
    source_case = next(case for case in fixtures if case['id'] == 'h01')
    saved = [json.loads(line) for line in
             (root / 'docs/evaluations/v0.13/regression/boundary-final/results.jsonl').read_text().splitlines()]
    case = next(case for case in saved if case['id'] == 'h01-targeted')
    hits = []
    for doc in source_case['documents']:
        text = doc['text']
        hits.append(dict(chunk_id='regression:' + doc['id'], document_id=doc['id'],
                         title=doc['title'], source=doc['source'], text=text, start=0, end=len(text),
                         content_sha256=hashlib.sha256(text.encode()).hexdigest()))
    review = next(generation for generation in case['raw_generations'] if generation['phase'] == 'review')
    raw = resolve_selections(json.loads(review['content']), hits)
    result = validate_assessments(case['question'], raw, hits)
    assert len(result['assessments']) == 3
    assert [item['verdict'] for item in result['assessments']] == ['supported', 'insufficient', 'insufficient']
    assert result['assessments'][0]['statement'] == '引用配置记录 precompute_ref_log_probs: false'
    assert '引用配置记录 r:' not in result['answer']
    assert '配置文件和代码逻辑表明' not in result['answer']
    assert 'false 会触发' not in result['answer']
    assert all(item['statement'] != 'Stage 2 配置中 precompute_ref_log_probs 为 false。'
               for item in result['assessments'])


def test_complete_protocol_line_keeps_measured_subject_and_both_limits_together():
    prefix = 'This local replay uses a fixed synthetic input sequence. ' * 5
    line = prefix + 'Acceptance requires latency at most 80 ms and failure rate at most 0.5 percent.'
    assert 300 < len(line) < 1000
    hit, _ = sample(line)
    catalog = evidence_catalog([hit])
    assert len(catalog) == 1
    assert next(iter(catalog.values()))['quote'] == line
    assert evidence_catalog_metadata()['max_quote_characters'] == 1000


def test_passage_hash_offsets_address_original_text_with_crlf_and_indentation():
    text = '\t Heading with spaces  \r\n\r\n  ' + 'Original sentence. ' * 80 + '  \r\nFinal observation.'
    hit, _ = sample(text)
    catalog = evidence_catalog([hit])
    assert catalog
    cursor = 0
    for eid, entry in catalog.items():
        quote = entry['quote']
        offset = text.index(quote, cursor)
        assert 3 <= len(quote) <= 1000
        assert eid == 'p' + hashlib.sha256(f'c:{offset}:{quote}'.encode()).hexdigest()[:16]
        cursor = offset + len(quote)
    assert ''.join(text.split()) == ''.join(''.join(entry['quote'].split()) for entry in catalog.values())
    assert evidence_catalog([hit]) == catalog


def test_long_line_uses_sentence_boundary_before_word_boundary():
    sentence = 'A measured observation belongs to this local replay. '
    hit, _ = sample(sentence * 30)
    quotes = [entry['quote'] for entry in evidence_catalog([hit]).values()]
    assert len(quotes) > 1 and all(len(quote) <= 1000 for quote in quotes)
    assert all(quote.endswith('.') for quote in quotes)


def test_long_sentence_uses_whitespace_without_splitting_measurement_word():
    line = 'measurement ' * 120
    hit, _ = sample(line)
    quotes = [entry['quote'] for entry in evidence_catalog([hit]).values()]
    assert len(quotes) > 1 and all(len(quote) <= 1000 for quote in quotes)
    assert all(quote.split() == ['measurement'] * len(quote.split()) for quote in quotes)


@pytest.mark.parametrize('length', [1000, 1001, 1002, 1999, 2001, 2505])
def test_unbroken_tokens_obey_quote_cap_without_losing_short_tail(length):
    text = '字' * length
    hit, _ = sample(text)
    quotes = [entry['quote'] for entry in evidence_catalog([hit]).values()]
    assert all(3 <= len(quote) <= 1000 for quote in quotes)
    assert ''.join(quotes) == text
