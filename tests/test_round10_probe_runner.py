"""Bounded diagnostic runner controls; all provider calls are local test doubles."""
import copy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from lablens import rag
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('round10_probe_runner', REPO / 'scripts/run_review_probe_round10.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """Use disposable inputs/plans and the real review/HTTP/telemetry stack."""
    base = tmp_path / 'docs/evaluations/conversation-round10'
    base.mkdir(parents=True)
    source = tmp_path / 'lablens/dummy.py'
    source.parent.mkdir()
    source.write_text('candidate = 1\n')
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'BASE', base)
    monkeypatch.setattr(probe, 'source_map', lambda: {'lablens/dummy.py': probe.sha(source)})
    monkeypatch.setattr(probe, 'git', lambda *args: 'a' * 40)
    monkeypatch.setenv('LLM_BASE_URL', probe.BASE_URL)
    monkeypatch.setenv('LLM_MODEL', probe.MODEL)
    monkeypatch.setenv('LLM_API_KEY', 'fake-unit-test-secret')
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '45')
    monkeypatch.setenv('RAG_TOTAL_SECONDS', '80')
    monkeypatch.setenv('EMBEDDING_BATCH_SIZE', '32')
    (base / 'protocol.md').write_text('Fixed diagnostic protocol.\n')
    (base / 'semantic-checklist.md').write_text('Fixed semantic checklist.\n')
    text = 'The retained receipt records final_status=SUCCEEDED.'
    hits = [dict(chunk_id='receipt', document_id='receipt-document', title='Retained receipt',
                 source='synthetic:receipt', text=text, start=0, end=len(text), content_sha256='test')]
    selection = {'assessments': [dict(statement='The recorded final status is SUCCEEDED.',
        verdict='supported', explanation=text, basis='historical_observation',
        evidence_ids=list(evidence_catalog(hits)))]}
    draft = resolve_selections(selection, hits)
    question = 'What final status does the retained receipt record?'
    context = {'scoped_review_contract': make_review_contract(question, draft)}
    payload = rag._generation_payload(question, hits, {'model': probe.MODEL}, draft=draft, context_data=context)
    cases = [dict(turn_id=tid, original_review_timeout_ms=10000, question=question, hits=hits,
                  draft=draft, context_data=context, original_payload=payload) for tid in probe.CASE_IDS]
    input_path = base / 'inputs.json'
    probe.dump(input_path, {'cases': cases})
    plan = dict(phase='baseline', candidate_sha='a' * 40,
        inputs_path=str(input_path.relative_to(tmp_path)), inputs_sha256=probe.sha(input_path),
        protocol_sha256=probe.sha(base / 'protocol.md'),
        checklist_sha256=probe.sha(base / 'semantic-checklist.md'), source_files=probe.source_map(),
        baseline_raw_sha256=None, model=probe.MODEL, provider_host='api.deepinfra.com',
        max_requests_phase=2, max_requests_experiment=4, max_tokens=2600, temperature=0,
        retry_enabled=False, embedding_enabled=False, created_utc=probe.now())
    probe.dump(base / 'baseline-plan.json', plan)
    calls = []

    def provider(url, **kwargs):
        calls.append(dict(url=url, **copy.deepcopy(kwargs)))
        rows = json.loads(kwargs['json']['messages'][1]['content'])['untrusted_draft']['assessments']
        response = {'retained_assessment_ids': [], 'assessments': [
            {key: value for key, value in row.items() if key != 'statement'} for row in rows]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(response)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx, 'post', provider)
    return dict(base=base, plan=plan, input_path=input_path, calls=calls, provider=provider,
                directory=base / 'runs/baseline')


@pytest.mark.parametrize('bad', [False, True, 0, -1, 40001, float('inf'), float('nan'), '10000'])
def test_invalid_allowance_is_rejected_before_claim(prepared, bad):
    data = probe.read(prepared['input_path'])
    data['cases'][0]['original_review_timeout_ms'] = bad
    probe.dump(prepared['input_path'], data)
    with pytest.raises(ValueError, match='budget'):
        probe.inputs(prepared['input_path'])
    assert not prepared['directory'].exists()
    assert prepared['calls'] == []


@pytest.mark.parametrize('case_ids', [['harbor-t1', 'cedar-t1'], ['cedar-t1', 'cedar-t1'], ['cedar-t1']])
def test_exact_declared_case_order_is_required(prepared, case_ids):
    data = probe.read(prepared['input_path'])
    data['cases'] = [dict(data['cases'][0], turn_id=tid) for tid in case_ids]
    probe.dump(prepared['input_path'], data)
    with pytest.raises(ValueError, match='predeclared'):
        probe.inputs(prepared['input_path'])


@pytest.mark.parametrize('base', [
    'http://api.deepinfra.com/v1/openai', 'https://user@api.deepinfra.com/v1/openai',
    'https://api.deepinfra.com:444/v1/openai', 'https://api.deepinfra.com/v1/openai?redirect=1',
    'https://api.deepinfra.com/v1/other', 'https://other.invalid/v1/openai',
])
def test_provider_endpoint_must_match_before_claim(prepared, monkeypatch, base):
    monkeypatch.setenv('LLM_BASE_URL', base)
    with pytest.raises(ValueError, match='configuration'):
        probe.run('baseline', 'GO')
    assert not prepared['directory'].exists()
    assert prepared['calls'] == []


def test_missing_secret_does_not_consume_claim(prepared, monkeypatch):
    monkeypatch.delenv('LLM_API_KEY')
    with pytest.raises(ValueError, match='configuration'):
        probe.run('baseline', 'GO')
    assert not prepared['directory'].exists()
    assert prepared['calls'] == []


def test_explicit_go_required(prepared):
    with pytest.raises(ValueError, match='Explicit GO'):
        probe.run('baseline', '')
    assert not prepared['directory'].exists()


@pytest.mark.parametrize('key,value', [('max_requests_phase', 3), ('max_requests_experiment', 5),
    ('max_tokens', 2601), ('temperature', 1), ('retry_enabled', True), ('embedding_enabled', True),
    ('model', 'different'), ('provider_host', 'other.invalid')])
def test_modified_plan_limits_are_rejected(prepared, key, value):
    plan = dict(prepared['plan'], **{key: value})
    with pytest.raises(ValueError, match='limits changed'):
        probe.verify(plan)


def test_semantic_checklist_change_is_rejected(prepared):
    (prepared['base'] / 'semantic-checklist.md').write_text('Changed after freeze.')
    with pytest.raises(ValueError, match='checklist'):
        probe.verify(prepared['plan'])


def test_source_inventory_change_is_rejected(prepared, monkeypatch):
    monkeypatch.setattr(probe, 'source_map', lambda: {})
    with pytest.raises(ValueError, match='inventory'):
        probe.verify(prepared['plan'])


def test_baseline_payload_difference_is_preflight_failure(prepared):
    data = probe.read(prepared['input_path'])
    data['cases'][1]['original_payload']['temperature'] = 1
    probe.dump(prepared['input_path'], data)
    plan = dict(prepared['plan'], inputs_sha256=probe.sha(prepared['input_path']))
    probe.dump(prepared['base'] / 'baseline-plan.json', plan)
    with pytest.raises(ValueError, match='historical reconstruction'):
        probe.run('baseline', 'GO')
    assert not prepared['directory'].exists()
    assert prepared['calls'] == []


def test_two_calls_capture_active_telemetry_and_seal_without_credentials(prepared):
    probe.run('baseline', 'GO')
    assert len(prepared['calls']) == 2
    assert httpx.post is prepared['provider']
    summary = probe.read(prepared['directory'] / 'summary.json')
    assert summary['fatal'] is None
    assert [row['outcome'] for row in summary['outcomes']] == ['review_returned'] * 2
    for tid in probe.CASE_IDS:
        case = prepared['directory'] / tid
        trace = probe.read(case / 'trace.json')
        events = [event['event'] for event in trace]
        assert events == ['stage_started', 'request_started', 'request_finished', 'stage_finished']
        start, finished = trace[1:3]
        assert start['phase'] == 'review' and start['step'] == 1 and start['stage_id'] == 's1'
        assert 0 < start['timeout_ms'] <= 10000
        assert finished['response_received'] is True and finished['usage_known'] is True
        assert finished['usage']['total_tokens'] == 15
        assert probe.read(case / 'attempt.json')['sent_requests'] == 1
        assert (case / 'provider-response.bin').exists()
    probe.verify_seal(prepared['directory'])
    for path in prepared['directory'].rglob('*'):
        if path.is_file():
            assert b'fake-unit-test-secret' not in path.read_bytes()
    with pytest.raises(FileExistsError):
        probe.run('baseline', 'GO')
    assert len(prepared['calls']) == 2


def test_timeout_preserves_unknown_usage_and_consumes_each_case_once(prepared, monkeypatch):
    attempts = []
    def timeout(url, **kwargs):
        attempts.append(url)
        raise httpx.ReadTimeout('Synthetic timeout')
    monkeypatch.setattr(httpx, 'post', timeout)
    probe.run('baseline', 'GO')
    assert len(attempts) == 2
    for tid in probe.CASE_IDS:
        case = prepared['directory'] / tid
        assert not (case / 'provider-response.bin').exists()
        usage = probe.read(case / 'usage.json')
        assert usage['provider_usage_unknown'] is True
        assert usage['generation_tokens']['total_tokens'] is None
        end = next(e for e in probe.read(case / 'trace.json') if e['event'] == 'request_finished')
        assert end['outcome'] == 'timeout'
        assert end['provider_acceptance'] == 'unknown'
        assert end['response_received'] is False
    probe.verify_seal(prepared['directory'])


def test_interrupt_seals_attempt_and_prevents_resume(prepared, monkeypatch):
    attempts = []
    def interrupted(url, **kwargs):
        attempts.append(url)
        raise KeyboardInterrupt()
    monkeypatch.setattr(httpx, 'post', interrupted)
    with pytest.raises(KeyboardInterrupt):
        probe.run('baseline', 'GO')
    assert attempts == [probe.BASE_URL + '/chat/completions']
    summary = probe.read(prepared['directory'] / 'summary.json')
    assert summary['fatal']['type'] == 'KeyboardInterrupt'
    assert summary['outcomes'] == [dict(case_id='cedar-t1',
        outcome='interrupted_or_unexpected_error', sent_requests=1)]
    usage = probe.read(prepared['directory'] / 'cedar-t1/usage.json')
    assert usage['provider_usage_unknown'] is True
    assert all(value is None for value in usage['generation_tokens'].values())
    assert not (prepared['directory'] / 'harbor-t1').exists()
    probe.verify_seal(prepared['directory'])
    with pytest.raises(FileExistsError):
        probe.run('baseline', 'GO')
    assert len(attempts) == 1


@pytest.mark.parametrize('mutation', ['url', 'redirects', 'timeout', 'payload'])
def test_last_moment_request_guard_prevents_outbound_mutation(prepared, monkeypatch, mutation):
    def bad_review(question, draft, hits, usage, config, **kwargs):
        payload = rag._generation_payload(question, hits, config, draft=draft,
                                          context_data=kwargs['context_data'])
        url = probe.BASE_URL + '/chat/completions'
        params = dict(json=payload, timeout=1, follow_redirects=False)
        if mutation == 'url': url = probe.BASE_URL + '/embeddings'
        elif mutation == 'redirects': params['follow_redirects'] = True
        elif mutation == 'timeout': params['timeout'] = 11
        elif mutation == 'payload': payload['max_tokens'] = 2601
        return httpx.post(url, **params)
    monkeypatch.setattr(rag, 'review_assessments', bad_review)
    with pytest.raises(ValueError, match='Unexpected extra'):
        probe.run('baseline', 'GO')
    assert prepared['calls'] == []
    assert probe.read(prepared['directory'] / 'cedar-t1/attempt.json')['sent_requests'] == 0
    probe.verify_seal(prepared['directory'])


def test_second_provider_request_is_blocked_and_first_is_preserved(prepared, monkeypatch):
    original_review = rag.review_assessments
    def doubled(question, draft, hits, usage, config, **kwargs):
        original_review(question, draft, hits, usage, config, **kwargs)
        return original_review(question, draft, hits, usage, config, **kwargs)
    monkeypatch.setattr(rag, 'review_assessments', doubled)
    probe.run('baseline', 'GO')
    assert len(prepared['calls']) == 2
    for tid in probe.CASE_IDS:
        case = prepared['directory'] / tid
        assert probe.read(case / 'attempt.json')['sent_requests'] == 1
        assert (case / 'provider-response.bin').exists()
        assert (case / 'failure.json').exists()
    probe.verify_seal(prepared['directory'])


def test_seal_rejects_added_and_modified_artifacts(prepared):
    probe.run('baseline', 'GO')
    directory = prepared['directory']
    extra = directory / 'unsealed.txt'
    extra.write_text('Unsealed addition.')
    with pytest.raises(ValueError, match='inventory'):
        probe.verify_seal(directory)
    extra.unlink()
    (directory / 'summary.json').write_text('{}')
    with pytest.raises(ValueError, match='file changed'):
        probe.verify_seal(directory)


@pytest.mark.parametrize('changed', ['inputs', 'protocol', 'checklist'])
def test_repaired_freeze_rejects_different_experiment_inputs(prepared, monkeypatch, changed):
    probe.run('baseline', 'GO')
    if changed == 'inputs':
        data = probe.read(prepared['input_path'])
        data['cases'][0]['question'] = 'A different question after baseline.'
        probe.dump(prepared['input_path'], data)
    else:
        filename = 'protocol.md' if changed == 'protocol' else 'semantic-checklist.md'
        (prepared['base'] / filename).write_text('Changed after baseline.')
    def committed_file(args, **kwargs):
        return (probe.ROOT / args[-1].split(':', 1)[1]).read_bytes()
    monkeypatch.setattr(probe.subprocess, 'check_output', committed_file)
    with pytest.raises(ValueError, match='same inputs, protocol and checklist'):
        probe.freeze('repaired', prepared['input_path'])
    assert not (prepared['base'] / 'repaired-plan.json').exists()


def test_repaired_same_inputs_has_separate_two_call_claim(prepared, monkeypatch):
    probe.run('baseline', 'GO')
    baseline_seal = probe.sha(prepared['directory'] / 'raw-seal.json')
    def committed_file(args, **kwargs):
        return (probe.ROOT / args[-1].split(':', 1)[1]).read_bytes()
    monkeypatch.setattr(probe.subprocess, 'check_output', committed_file)
    probe.freeze('repaired', prepared['input_path'])
    plan = probe.read(prepared['base'] / 'repaired-plan.json')
    assert plan['baseline_raw_sha256'] == baseline_seal
    assert plan['inputs_sha256'] == prepared['plan']['inputs_sha256']
    probe.run('repaired', 'GO')
    assert len(prepared['calls']) == 4
    probe.verify_seal(prepared['base'] / 'runs/repaired')
    assert probe.sha(prepared['directory'] / 'raw-seal.json') == baseline_seal
    with pytest.raises(FileExistsError):
        probe.run('repaired', 'GO')
    assert len(prepared['calls']) == 4
