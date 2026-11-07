"""All transport is fake: these tests never contact or bill a provider."""
import copy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from lablens import rag

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('round13_probe', REPO / 'scripts/run_model_probe_round13.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    base = tmp_path / 'docs/evaluations/conversation-round13/model'
    base.mkdir(parents=True)
    for name in ('fixture.json', 'protocol.md', 'semantic-checklist.md'):
        (base / name).write_bytes((probe.BASE / name).read_bytes())
    source = tmp_path / 'lablens/dummy.py'
    source.parent.mkdir()
    source.write_text('frozen = True\n')
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'BASE', base)
    monkeypatch.setattr(probe, 'source_map', lambda: {'lablens/dummy.py': probe.sha(source)})
    monkeypatch.setattr(probe, 'git', lambda *args: 'a' * 40)
    monkeypatch.setenv('LLM_MODEL', probe.MODEL)
    monkeypatch.setenv('LLM_BASE_URL', probe.BASE_URL)
    monkeypatch.setenv('LLM_API_KEY', 'unit-test-private-key')
    monkeypatch.setenv('HTTPS_PROXY', 'https://proxy-user:private-proxy-pass@example.invalid')
    prices = dict(provider_base_url=probe.BASE_URL, model=probe.MODEL,
        source_url='https://deepinfra.com/' + probe.MODEL, checked_on='2026-11-08', context_tokens=262144,
        input_usd_per_million_upper=.135, output_usd_per_million_upper=.825, cost_cap_usd=1)
    probe.dump(base / 'pricing.json', prices)
    payloads, retrieval = probe.payloads()
    _, bound = probe.pricing(base / 'pricing.json')
    plan = dict(candidate_sha='a' * 40, source_files=probe.source_map(), versions=probe.versions(),
        frozen_files={str((base / name).relative_to(tmp_path)): probe.sha(base / name)
                      for name in ('fixture.json', 'protocol.md', 'semantic-checklist.md', 'pricing.json')},
        pricing=prices, pricing_path=str((base / 'pricing.json').relative_to(tmp_path)),
        planned_cost_upper_usd=bound, request_payloads=payloads, offline_retrieval=retrieval,
        model=probe.MODEL, provider_base_url=probe.BASE_URL, max_requests=3,
        timeouts_seconds=[10, 35, 40], max_output_tokens=[64, 2600, 2600],
        max_body_bytes=128 * 1024, total_seconds=100, retry_enabled=False, embedding_enabled=False)
    probe.dump(base / 'plan.json', plan)
    calls = []
    def provider(url, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            content = 'OK'
        else:
            passages = json.loads(kwargs['json']['messages'][1]['content'])['passages']
            receipt = next(key for key, value in passages.items() if 'broker_confirm_received=true' in value['quote'])
            content = json.dumps(dict(assessments=[dict(
                statement='The publisher receipt records that a broker confirm was received.',
                verdict='supported', basis='historical_observation', evidence_ids=[receipt],
                explanation='The retained synthetic publisher receipt explicitly records broker_confirm_received=true.')]))
        return httpx.Response(200, json=dict(choices=[dict(finish_reason='stop', message=dict(content=content))],
            usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)), request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', provider)
    return dict(base=base, directory=base / 'run', plan=plan, calls=calls, provider=provider, source=source)


def test_three_calls_use_real_rag_validation_and_capture_response_before_seal(prepared):
    probe.run('GO')
    assert len(prepared['calls']) == 3
    assert [call['json']['max_tokens'] for call in prepared['calls']] == [64, 2600, 2600]
    assert all(0 < call['timeout'] <= cap for call, cap in zip(prepared['calls'], [10, 35, 40]))
    result = probe.read(prepared['directory'] / 'result.json')
    assert result['status'] == 'answered'
    assert result['generation_review']['calls'] == 1
    assert len(result['retrieval']['hits']) == 4
    assert result['usage']['model_calls'] == 2
    assert result['usage']['embedding_requests'] == 0
    assert probe.read(prepared['directory'] / 'usage.json')['aggregate_tokens']['total_tokens'] == 45
    assert probe.read(prepared['directory'] / 'summary.json')['final_semantic_status'] == 'not_scored'
    probe.verify_seal(prepared['directory'])
    for path in prepared['directory'].rglob('*'):
        if path.is_file():
            assert b'unit-test-private-key' not in path.read_bytes()
            assert b'private-proxy-pass' not in path.read_bytes()
    assert probe.read(prepared['directory'] / 'transport-configuration.json')['proxy_environment_present']['HTTPS_PROXY']
    with pytest.raises(FileExistsError):
        probe.run('GO')
    assert len(prepared['calls']) == 3


@pytest.mark.parametrize('failure', [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout])
def test_tiny_timeout_stops_after_one_attempt_and_preserves_unknown_usage(prepared, monkeypatch, failure):
    calls = []
    def provider(url, **kwargs):
        calls.append(url)
        raise failure('Do not publish private-proxy-pass')
    monkeypatch.setattr(httpx, 'post', provider)
    probe.run('GO')
    assert len(calls) == 1
    summary = probe.read(prepared['directory'] / 'summary.json')
    assert summary['outcome'] == 'tiny_failed'
    assert summary['dependent_rag'] == 'not_run'
    assert summary['attempts'][0]['exception_types'] == [failure.__name__]
    usage = probe.read(prepared['directory'] / 'usage.json')
    assert usage['aggregate_tokens']['total_tokens'] is None
    assert usage['tiny']['generation_tokens']['total_tokens'] is None
    probe.verify_seal(prepared['directory'])


@pytest.mark.parametrize('phase', ['tiny', 'generation'])
def test_missing_usage_blocks_dependent_calls(prepared, monkeypatch, phase):
    original = prepared['provider']
    def provider(url, **kwargs):
        response = original(url, **kwargs)
        if len(prepared['calls']) == (1 if phase == 'tiny' else 2):
            body = response.json()
            body.pop('usage')
            return httpx.Response(200, json=body, request=response.request)
        return response
    monkeypatch.setattr(httpx, 'post', provider)
    probe.run('GO')
    assert len(prepared['calls']) == (1 if phase == 'tiny' else 2)
    assert probe.read(prepared['directory'] / 'usage.json')['aggregate_tokens']['total_tokens'] is None
    assert probe.read(prepared['directory'] / 'summary.json')['final_semantic_status'] == 'not_evaluable'


def test_generation_bad_json_is_not_retried_and_raw_body_retained(prepared, monkeypatch):
    original = prepared['provider']
    def provider(url, **kwargs):
        response = original(url, **kwargs)
        return httpx.Response(200, content=b'invalid json', request=response.request) if len(prepared['calls']) == 2 else response
    monkeypatch.setattr(httpx, 'post', provider)
    probe.run('GO')
    assert len(prepared['calls']) == 2
    assert (prepared['directory'] / '02-generation/provider-response.bin').read_bytes() == b'invalid json'
    assert probe.read(prepared['directory'] / 'result.json')['status'] == 'provider_error'


def test_interruption_seals_unknown_attempt_and_cannot_resume(prepared, monkeypatch):
    def provider(url, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(httpx, 'post', provider)
    with pytest.raises(KeyboardInterrupt): probe.run('GO')
    summary = probe.read(prepared['directory'] / 'summary.json')
    assert summary['fatal']['exception_types'] == ['KeyboardInterrupt']
    assert summary['sent_requests'] == 1
    assert probe.read(prepared['directory'] / 'usage.json')['tiny']['generation_tokens']['total_tokens'] is None
    probe.verify_seal(prepared['directory'])
    with pytest.raises(FileExistsError): probe.run('GO')


def test_diagnostic_write_failure_does_not_replace_transport_exception(prepared, monkeypatch):
    def provider(url, **kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(httpx, 'post', provider)
    original_dump = probe.dump
    def broken_dump(path, value, **kwargs):
        if Path(path).name in ('transport-failure.json', 'attempt.json', 'trace.json'):
            raise OSError('disk problem')
        return original_dump(path, value, **kwargs)
    monkeypatch.setattr(probe, 'dump', broken_dump)
    with pytest.raises(KeyboardInterrupt): probe.run('GO')
    assert (prepared['directory'] / 'once-only-claim.json').exists()


@pytest.mark.parametrize('mutation', ['source', 'rubric', 'fixture', 'protocol', 'limits'])
def test_prefreeze_mutation_stops_without_claim_or_calls(prepared, mutation):
    if mutation == 'source': prepared['source'].write_text('changed = True')
    elif mutation == 'limits':
        plan = dict(prepared['plan'], max_requests=4)
        probe.dump(prepared['base'] / 'plan.json', plan)
    else:
        name = {'rubric': 'semantic-checklist.md', 'fixture': 'fixture.json', 'protocol': 'protocol.md'}[mutation]
        (prepared['base'] / name).write_text('changed')
    with pytest.raises(ValueError): probe.run('GO')
    assert not prepared['directory'].exists()
    assert prepared['calls'] == []


@pytest.mark.parametrize('bad', [0, -1, True, '0.1', float('nan'), float('inf')])
def test_invalid_pricing_rejected(prepared, bad):
    path = prepared['base'] / 'pricing.json'
    value = probe.read(path)
    value['input_usd_per_million_upper'] = bad
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): probe.pricing(path)


def test_cost_upper_bound_is_full_context_not_byte_token_estimate(prepared):
    _, bound = probe.pricing(prepared['base'] / 'pricing.json')
    assert bound == pytest.approx(.11051112)


def test_missing_credentials_and_go_do_not_consume_claim(prepared, monkeypatch):
    with pytest.raises(ValueError, match='Explicit GO'): probe.run('')
    monkeypatch.delenv('LLM_API_KEY')
    with pytest.raises(ValueError, match='configuration'): probe.run('GO')
    assert not prepared['directory'].exists()


def test_fourth_call_is_blocked_before_transport(prepared, monkeypatch):
    original = rag.answer_rag
    def doubled(request, **kwargs):
        result = original(request, **kwargs)
        if request.mode == 'live':
            second = original(request, **kwargs)
            assert second['status'] == 'provider_error'
        return result
    monkeypatch.setattr(rag, 'answer_rag', doubled)
    probe.run('GO')
    assert len(prepared['calls']) == 3


def test_stale_runtime_does_not_consume_claim(prepared, monkeypatch):
    monkeypatch.setattr(probe, 'versions', lambda: {'httpx': 'different'})
    with pytest.raises(ValueError, match='dependency'): probe.run('GO')
    assert not prepared['directory'].exists()


def test_total_deadline_stops_after_late_tiny_response(prepared, monkeypatch):
    value = [0.0]
    monkeypatch.setattr(probe.time, 'monotonic', lambda: value[0])
    original = prepared['provider']
    def late(url, **kwargs):
        response = original(url, **kwargs)
        value[0] = 101.0
        return response
    monkeypatch.setattr(httpx, 'post', late)
    probe.run('GO')
    assert len(prepared['calls']) == 1
    assert probe.read(prepared['directory'] / 'summary.json')['dependent_rag'] == 'not_run'
    # The late successful response still reported usage; do not erase it.
    assert probe.read(prepared['directory'] / 'usage.json')['aggregate_tokens']['total_tokens'] == 15


def test_http_error_retains_raw_response_but_does_not_promote_usage(prepared, monkeypatch):
    original = prepared['provider']
    def failed(url, **kwargs):
        response = original(url, **kwargs)
        return httpx.Response(429, json=response.json(), request=response.request)
    monkeypatch.setattr(httpx, 'post', failed)
    probe.run('GO')
    assert len(prepared['calls']) == 1
    assert (prepared['directory'] / '01-tiny/provider-response.bin').exists()
    assert probe.read(prepared['directory'] / 'usage.json')['aggregate_tokens']['total_tokens'] is None
