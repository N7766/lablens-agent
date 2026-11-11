"""Offline acceptance-driver guards: no JVM, provider, GPU or live gateway calls."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('private_agent_acceptance', ROOT / 'scripts/verify_private_agent.py')
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture
def candidate(tmp_path):
    output = tmp_path / 'candidate'
    output.mkdir()
    snapshot = {'revision': 2, 'documents': driver.DOCUMENTS}
    plan = dict(version=driver.VERSION, base_url='http://127.0.0.1:8086',
                workspace_id='synthetic-workspace', conversation_id='synthetic-conversation',
                case='report', question=driver.QUESTION, revision=2,
                snapshot_sha256=driver.sha(driver.canonical(snapshot)),
                index_id='synthetic-index', documents_sha256='d' * 64,
                source_files=driver.sources(), review_protocol=driver.REVIEW_PROTOCOL,
                gateway_plan_sha256=None, evidence_kind='local_scripted',
                planned_max_model_requests=3, submission_attempts=1, retries=0,
                idempotency_key='synthetic-idempotency', scope=driver.SCOPE)

    def seal(**changes):
        plan.update(changes)
        raw = driver.canonical(plan)
        (output / 'plan.json').write_bytes(raw)
        (output / 'plan.sha256').write_text(driver.sha(raw) + '\n')
        return raw

    seal()
    return output, plan, snapshot, seal


def forbid_session(*args, **kwargs):
    raise AssertionError('Rejected plans must not reach the application session')


def scripted_session(monkeypatch, snapshot, *, submit_error=None):
    calls = []

    class Client:
        def get(self, path):
            calls.append(('GET', path))
            assert path.endswith('/documents')
            return httpx.Response(200, json=snapshot, request=httpx.Request('GET', 'http://127.0.0.1:8086' + path))

        def post(self, path, **kwargs):
            calls.append(('POST', path, kwargs))
            assert path.endswith('/turns')
            if submit_error is not None:
                raise submit_error
            raise AssertionError('This test must not submit')

    @contextmanager
    def session(_):
        yield Client()

    monkeypatch.setattr(driver, 'session', session)
    return calls


def test_source_drift_rejected_before_run_directory_or_network(candidate, monkeypatch):
    output, plan, _, _ = candidate
    changed = dict(plan['source_files'])
    changed['lablens/rag.py'] = '0' * 64
    monkeypatch.setattr(driver, 'sources', lambda: changed)
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='plan/source drift'):
        driver.run(output, 'local_scripted')
    assert not (output / 'run').exists()


def test_unsealed_plan_change_rejected(candidate, monkeypatch):
    output, _, _, _ = candidate
    with (output / 'plan.json').open('ab') as stream:
        stream.write(b' ')
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='plan/source drift'):
        driver.run(output, 'local_scripted')
    assert not (output / 'run').exists()


@pytest.mark.parametrize('frozen_kind,requested_kind', [
    ('local_scripted', 'real_private_model'), ('real_private_model', 'local_scripted'),
])
def test_evidence_kind_cannot_override_frozen_plan(candidate, monkeypatch, frozen_kind, requested_kind):
    output, _, _, seal = candidate
    seal(evidence_kind=frozen_kind, gateway_plan_sha256='a' * 64 if frozen_kind == 'real_private_model' else None)
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='cannot override'):
        driver.run(output, requested_kind)
    assert not (output / 'run').exists()


@pytest.mark.parametrize('mutation', ['replace', 'delete'])
def test_run_keeps_once_verified_plan_bytes_and_digest(candidate, monkeypatch, mutation):
    output, plan, _, _ = candidate
    path = output / 'plan.json'
    original = path.read_bytes()
    real_read = Path.read_bytes
    reads = []

    def read_once(self):
        raw = real_read(self)
        if self == path:
            reads.append(raw)
            if mutation == 'replace':
                self.write_text('{"changed_after_read":true}')
            else:
                self.unlink()
        return raw

    monkeypatch.setattr(Path, 'read_bytes', read_once)
    calls = scripted_session(monkeypatch, {'revision': plan['revision'] + 1, 'documents': []})
    with pytest.raises(ValueError, match='Workspace changed after freeze'):
        driver.run(output, 'local_scripted')
    assert reads == [original]
    assert (output / 'run/frozen-plan.json').read_bytes() == original
    assert json.loads((output / 'run/intent.json').read_text())['plan_sha256'] == driver.sha(original)
    assert len(calls) == 1 and calls[0][0] == 'GET'


@pytest.mark.parametrize('failure', [
    httpx.ReadTimeout('Synthetic unknown admission'),
    httpx.ReadError('Synthetic disconnected admission'),
])
def test_unknown_submission_is_not_retried_or_resubmitted(candidate, monkeypatch, failure):
    output, plan, snapshot, _ = candidate
    calls = scripted_session(monkeypatch, snapshot, submit_error=failure)
    with pytest.raises(type(failure)):
        driver.run(output, 'local_scripted')
    assert len([call for call in calls if call[0] == 'POST']) == 1
    submit = json.loads((output / 'run/submit-intent.json').read_text())
    assert submit['idempotency_key'] == plan['idempotency_key']
    assert not (output / 'run/admission.json').exists()
    assert not (output / 'run/summary.json').exists()
    with pytest.raises(FileExistsError):
        driver.run(output, 'local_scripted')
    assert len([call for call in calls if call[0] == 'POST']) == 1


@pytest.fixture
def gateway_http(monkeypatch):
    observations = []
    status = dict(plan_sha256='a' * 64, phase='agent', upstream_mode='ssh_tunnel',
                  blocked=False, reserved=0, completed=0)
    real_client = httpx.Client

    def handler(request):
        observations.append(request)
        assert str(request.url) == 'http://127.0.0.1:18081/health'
        assert request.method == 'GET'
        assert request.headers['Authorization'] == 'Bearer synthetic-gateway-key'
        return httpx.Response(200, json=status)

    def client(**kwargs):
        assert kwargs['trust_env'] is False
        assert kwargs['timeout'] == 3
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setenv('LABLENS_PRIVATE_GATEWAY_KEY', 'synthetic-gateway-key')
    monkeypatch.setattr(driver.httpx, 'Client', client)
    return status, observations


@pytest.mark.parametrize('field,value', [
    ('plan_sha256', 'b' * 64), ('phase', 'probe'), ('upstream_mode', 'scripted'), ('blocked', True),
])
def test_live_gateway_mismatch_blocks_before_application_or_submission(candidate, monkeypatch, gateway_http, field, value):
    output, _, _, seal = candidate
    seal(evidence_kind='real_private_model', gateway_plan_sha256='a' * 64)
    status, requests = gateway_http
    status[field] = value
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='Live gateway does not match'):
        driver.run(output, 'real_private_model')
    assert len(requests) == 1
    assert not (output / 'run/submit-intent.json').exists()


@pytest.mark.parametrize('field,value', [('reserved', 1), ('completed', 1), ('reserved', None), ('completed', None)])
def test_already_used_or_unknown_gateway_allocation_never_submits(candidate, monkeypatch, gateway_http, field, value):
    output, _, _, seal = candidate
    seal(evidence_kind='real_private_model', gateway_plan_sha256='a' * 64)
    status, requests = gateway_http
    status[field] = value
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='allocation was already used'):
        driver.run(output, 'real_private_model')
    assert len(requests) == 1
    assert (output / 'run/gateway-before.json').exists()
    assert not (output / 'run/submit-intent.json').exists()
    assert 'synthetic-gateway-key' not in (output / 'run/gateway-before.json').read_text()


def test_missing_gateway_binding_never_contacts_gateway_or_application(candidate, monkeypatch):
    output, _, _, seal = candidate
    seal(evidence_kind='real_private_model')
    monkeypatch.setattr(driver, 'session', forbid_session)
    with pytest.raises(ValueError, match='bound during preparation'):
        driver.run(output, 'real_private_model')
    assert not (output / 'run/submit-intent.json').exists()


@pytest.mark.parametrize('case', ['insufficient', 'timeout'])
def test_real_model_only_allows_frozen_report_case(candidate, monkeypatch, case):
    output, _, _, seal = candidate
    seal(case=case, evidence_kind='real_private_model', gateway_plan_sha256='a' * 64)
    monkeypatch.setattr(driver, 'session', forbid_session)
    monkeypatch.setattr(driver, 'gateway_observation', lambda *_: pytest.fail('Case rejected before gateway access'))
    with pytest.raises(ValueError, match='one report case only'):
        driver.run(output, 'real_private_model')
    assert not (output / 'run/submit-intent.json').exists()


def test_blocked_gateway_after_run_is_recordable_but_wrong_identity_is_rejected(candidate, gateway_http):
    _, plan, _, seal = candidate
    seal(evidence_kind='real_private_model', gateway_plan_sha256='a' * 64)
    status, _ = gateway_http
    status.update(blocked=True, reserved=3, completed=2)
    assert driver.gateway_observation(plan, require_active=False) == status
    status['plan_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='Live gateway does not match'):
        driver.gateway_observation(plan, require_active=False)
