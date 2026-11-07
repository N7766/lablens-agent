"""Crash/reconstruction and journal tests; scripted decisions are not model evals."""
import copy
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from lablens import checkpoints, workspace_agent
from lablens.cancellation import CancellationRegistry
from lablens.checkpoints import CheckpointError, ResumeContext, canonical
from lablens.workspace_agent import WorkspaceAgent
from test_workspace_agent import DecisionModel, controlled_review, request, retrieval_server


class SimulatedProcessDeath(BaseException):
    pass


class Journal:
    enabled = True
    def __init__(self, *, stop_after=None, fail_operation=None):
        self.boundaries = []
        self.operations = []
        self.stop_after = stop_after
        self.fail_operation = fail_operation
        self.revision = 0
    def boundary(self, state, *, timeout):
        assert timeout > 0
        self.revision += 1
        self.boundaries.append((self.revision, copy.deepcopy(state)))
        if len(self.boundaries) == self.stop_after:
            raise SimulatedProcessDeath()
    def operation(self, phase, *, step, timeout):
        self.revision += 1
        self.operations.append((phase, step))
        if len(self.operations) == self.fail_operation:
            raise CheckpointError('Synthetic unavailable journal')


def resume_from(journal, req):
    revision, state = journal.boundaries[-1]
    payload = canonical(state)
    return req.model_copy(update={'resume_context': ResumeContext(parent_task_id=str(uuid4()),
        checkpoint_revision=revision, state_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        state={'payload_json': payload})})


def change_state(req, change):
    state = json.loads(req.resume_context.state.payload_json)
    change(state)
    payload = canonical(state)
    return req.model_copy(update={'resume_context': req.resume_context.model_copy(update={
        'state': checkpoints.CheckpointState(payload_json=payload),
        'state_sha256': hashlib.sha256(payload.encode()).hexdigest()})})


def freeze_after_search(monkeypatch):
    journal = Journal(stop_after=2)
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    req = request()
    model = DecisionModel()
    with pytest.raises(SimulatedProcessDeath):
        WorkspaceAgent(model).run(req)
    assert model.count == 1
    assert journal.operations == [('decision', 1)]
    return resume_from(journal, req), journal


def test_fresh_worker_restores_complete_search_without_repeating_it(monkeypatch, controlled_review):
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        req, old = freeze_after_search(monkeypatch)
        journal = Journal()
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
        # A new process/model has no old mutable Python state. Script starts at
        # the next decision; it receives the restored source/tool messages.
        model = DecisionModel(); model.count = 1
        result = WorkspaceAgent(model).run(req)
    assert result['status'] == 'answered'
    assert len(seen) == 1
    assert result['usage']['model_calls'] == 4  # three decisions plus review
    assert result['agent']['tool_calls'] == 3
    assert result['usage_scope'] == 'cumulative_original_and_resumed_execution'
    assert result['recovery']['restored_model_calls'] == 1
    assert result['recovery']['restored_tool_calls'] == 1
    assert result['recovery']['restored_next_step'] == 2
    assert result['recovery']['restored_elapsed_ms'] >= 3000
    assert result['latency_ms'] >= result['recovery']['restored_elapsed_ms']
    assert journal.operations == [('decision', 2), ('decision', 3), ('review', 3)]
    assert [state['next_step'] for _, state in journal.boundaries] == [2, 3]
    assert len(controlled_review) == 1
    assert result['retrieval']['searches'] == old.boundaries[-1][1]['result']['retrieval']['searches']


def test_pending_operation_recorded_before_model_and_no_retry_after_failed_ack(monkeypatch):
    journal = Journal(fail_operation=1)
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    model = DecisionModel()
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_checkpoint_unavailable'
    assert model.count == 0
    assert result['usage']['model_calls'] == 0
    assert len(journal.boundaries) == 1
    assert journal.operations == [('decision', 1)]


@pytest.mark.parametrize('mutation', [
    lambda value: value['observed'][next(iter(value['observed']))].update(text='forged source'),
    lambda value: value['exchanges'].pop(),
    lambda value: value['result']['usage'].update(model_calls=0),
    lambda value: value['result']['usage'].update(budget_exhausted=True),
    lambda value: value.update(next_step=1),
    lambda value: value.update(elapsed_ms=-1),
    lambda value: value.update(config_sha256='0' * 64),
    lambda value: value.update(request_sha256='0' * 64),
    lambda value: value.update(call_ids=[]),
    lambda value: value['result'].update(question='different question'),
    lambda value: value['result']['agent'].update(implementation_sha256='0' * 64),
])
def test_invalid_checkpoint_stops_before_any_new_model_or_tool(monkeypatch, mutation):
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        req, _ = freeze_after_search(monkeypatch)
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: Journal())
        model = DecisionModel()
        result = WorkspaceAgent(model).run(change_state(req, mutation))
    assert result['status'] == 'agent_invalid_checkpoint'
    assert result['usage_scope'] == 'current_execution_only_before_checkpoint_restore'
    assert result['usage']['model_calls'] == 0
    assert model.count == 0
    assert len(seen) == 1


def test_configuration_change_rejects_saved_boundary(monkeypatch):
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        req, _ = freeze_after_search(monkeypatch)
        monkeypatch.setenv('LLM_MODEL', 'changed-fixture-config')
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: Journal())
        model = DecisionModel()
        result = WorkspaceAgent(model).run(req)
    assert result['status'] == 'agent_invalid_checkpoint'
    assert model.count == 0


def test_exhausted_saved_active_budget_does_not_reset(monkeypatch):
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        req, _ = freeze_after_search(monkeypatch)
        req = change_state(req, lambda value: value.update(elapsed_ms=75000))
        journal = Journal()
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
        model = DecisionModel()
        result = WorkspaceAgent(model).run(req)
    assert result['status'] in {'agent_timeout', 'agent_checkpoint_unavailable'}
    assert model.count == 0
    assert result['usage']['model_calls'] == 1
    assert len(seen) == 1


def test_partial_tool_batch_has_no_resumable_boundary(monkeypatch):
    class BatchModel(DecisionModel):
        def complete(self, messages, tools):
            message, usage = super().complete(messages, tools)
            second = copy.deepcopy(message['tool_calls'][0]); second['id'] = 'second'
            message['tool_calls'].append(second)
            return message, usage
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        journal = Journal()
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
        class CrashClient:
            calls = 0
            def post(self, url, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise SimulatedProcessDeath()
                return httpx.post(url, **kwargs, trust_env=False)
        with pytest.raises(SimulatedProcessDeath):
            WorkspaceAgent(BatchModel(), client=CrashClient()).run(request())
    assert len(journal.boundaries) == 1
    assert journal.boundaries[0][1]['next_step'] == 1
    assert journal.operations == [('decision', 1)]


def test_resume_without_fenced_execution_rejected(monkeypatch):
    with retrieval_server() as (base, _):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        req, _ = freeze_after_search(monkeypatch)
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', checkpoints.CheckpointJournal)
        model = DecisionModel()
        result = WorkspaceAgent(model).run(req)
    assert result['status'] == 'agent_invalid_checkpoint'
    assert model.count == 0


def test_journal_requires_authenticated_owner_even_in_development(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'false')
    monkeypatch.setenv('LABLENS_CHECKPOINT_BASE_URL', 'http://127.0.0.1:8086')
    registry = CancellationRegistry()
    with registry.execution(str(uuid4())), checkpoints.execution_owner(str(uuid4())):
        with pytest.raises(CheckpointError):
            checkpoints.CheckpointJournal()


def test_journal_payload_digest_and_lost_ack_fail_closed(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', 'synthetic-only-' + 'x' * 32)
    monkeypatch.setenv('LABLENS_CHECKPOINT_BASE_URL', 'http://127.0.0.1:8086')
    registry = CancellationRegistry()
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        body = kwargs['json']
        if len(calls) == 2:
            raise httpx.ReadTimeout('synthetic lost receipt')
        return httpx.Response(200, json={'revision': body['revision'], 'state_sha256': body['state_sha256']},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(checkpoints, 'bounded_post', post)
    tid, owner = str(uuid4()), str(uuid4())
    with registry.execution(tid), checkpoints.execution_owner(owner):
        journal = checkpoints.CheckpointJournal()
        journal.boundary({'unicode': '资料', 'score': 0.01823456789012345}, timeout=10)
        with pytest.raises(CheckpointError):
            journal.operation('decision', step=1, timeout=10)
    assert len(calls) == 2
    assert journal.revision == 1
    url, args = calls[0]
    assert url == f'http://127.0.0.1:8086/api/internal/tasks/{tid}/checkpoint'
    assert args['headers']['X-LabLens-Execution-Owner'] == owner
    payload = args['json']['state']['payload_json']
    assert hashlib.sha256(payload.encode()).hexdigest() == args['json']['state_sha256']
    assert args['timeout'] == 3
    assert args['trust_env'] is False
    assert calls[1][1]['json']['kind'] == 'operation_started'


@pytest.mark.parametrize('url', ['https://example.com', 'http://localhost:8086', 'http://127.0.0.1:8086/path',
                                  'http://user:pass@127.0.0.1:8086', 'http://127.0.0.1:8086/?x=1'])
def test_callback_endpoint_cannot_be_remote_or_request_controlled(monkeypatch, url):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', 'synthetic-only-' + 'x' * 32)
    monkeypatch.setenv('LABLENS_CHECKPOINT_BASE_URL', url)
    registry = CancellationRegistry()
    with registry.execution(str(uuid4())), checkpoints.execution_owner(str(uuid4())):
        with pytest.raises(CheckpointError):
            checkpoints.CheckpointJournal()


def test_expired_memory_is_rechecked_before_resumed_provider_work(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from lablens import memory_context
    from test_workspace_memory import remembered_request, entry
    from lablens.workspace_agent import WorkspaceAgentRequest
    now = datetime.now(timezone.utc)
    data = remembered_request()
    entry(data)['expires_at'] = (now + timedelta(hours=1)).isoformat()
    req = WorkspaceAgentRequest.model_validate(data)
    journal = Journal(stop_after=2)
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        with pytest.raises(SimulatedProcessDeath):
            WorkspaceAgent(DecisionModel()).run(req)
        resumed = resume_from(journal, req)
        class Later:
            @staticmethod
            def now(tz): return now + timedelta(hours=2)
            fromisoformat = staticmethod(datetime.fromisoformat)
        monkeypatch.setattr(memory_context, 'datetime', Later)
        model = DecisionModel()
        result = WorkspaceAgent(model).run(resumed)
    assert result['status'] == 'invalid_memory_context'
    assert result['usage_scope'] == 'current_execution_only_before_checkpoint_restore'
    assert model.count == 0
    assert len(seen) == 1


def test_model_failure_keeps_pending_operation_without_new_safe_boundary(monkeypatch):
    from lablens.model import ModelError
    journal = Journal()
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    class FailedModel(DecisionModel):
        def complete(self, messages, tools):
            self.count += 1
            raise ModelError('Provider request timed out')
    model = FailedModel()
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_timeout'
    assert model.count == 1
    assert result['usage']['provider_usage_unknown'] is True
    assert len(journal.boundaries) == 1
    assert journal.operations == [('decision', 1)]


def test_checkpoint_limit_and_missing_ack_do_not_issue_another_request(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'true')
    monkeypatch.setenv('LABLENS_SERVICE_KEY', 'synthetic-only-' + 'x' * 32)
    monkeypatch.setenv('LABLENS_CHECKPOINT_BASE_URL', 'http://127.0.0.1:8086')
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={}, request=httpx.Request('POST', url))
    monkeypatch.setattr(checkpoints, 'bounded_post', post)
    registry = CancellationRegistry()
    with registry.execution(str(uuid4())), checkpoints.execution_owner(str(uuid4())):
        journal = checkpoints.CheckpointJournal()
        with pytest.raises(CheckpointError):
            journal.boundary({'large': '资料' * checkpoints.MAX_BYTES}, timeout=10)
        assert calls == []
        with pytest.raises(CheckpointError):
            journal.boundary({'small': True}, timeout=10)
        assert len(calls) == 1
        assert journal.revision == 0


def test_execution_owner_header_requires_root_agent_before_dispatch(monkeypatch):
    from fastapi.testclient import TestClient
    from lablens import rag_api
    from lablens.api import app
    monkeypatch.setattr(rag_api, '_answer', lambda *args: pytest.fail('must reject before dispatch'))
    client = TestClient(app)
    owner = {'X-LabLens-Execution-Owner': str(uuid4())}
    assert client.post('/api/rag/answer', json=request().model_dump(), headers=owner).status_code == 422
    data = request().model_dump(exclude={'memory_context', 'resume_context', 'conversation_context'})
    data['mode'] = 'extractive'
    assert client.post('/api/rag/answer', json=data,
        headers={**owner, 'X-LabLens-Task-Id': str(uuid4())}).status_code == 422


@pytest.mark.parametrize('status,code', [('agent_checkpoint_unavailable', 503), ('agent_invalid_checkpoint', 422)])
def test_checkpoint_failure_api_status(monkeypatch, status, code):
    from fastapi.testclient import TestClient
    from lablens import rag_api
    from lablens.api import app
    monkeypatch.setattr(rag_api, '_answer', lambda *args: {'status': status, 'usage': {}})
    response = TestClient(app).post('/api/rag/answer', json=request().model_dump())
    assert response.status_code == code
    assert response.json()['detail']['status'] == status


def test_completed_result_with_unknown_billing_preserves_unknown_usage_on_resume(monkeypatch, controlled_review):
    class MissingUsageModel(DecisionModel):
        def complete(self, messages, tools):
            message, usage = super().complete(messages, tools)
            return message, {} if self.count == 1 else usage
    journal = Journal(stop_after=2)
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    req = request()
    with retrieval_server() as (base, seen):
        monkeypatch.setenv('LABLENS_AGENT_TOOL_BASE_URL', base)
        with pytest.raises(SimulatedProcessDeath):
            WorkspaceAgent(MissingUsageModel()).run(req)
        resumed = resume_from(journal, req)
        monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: Journal())
        # The controlled final review also preserves an unknown aggregate.
        from lablens import rag
        previous_review = rag.review_assessments
        def review(question, raw, hits, usage, config, **kwargs):
            tokens = usage['generation_tokens']
            usage['generation_tokens'] = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
            result = previous_review(question, raw, hits, usage, config, **kwargs)
            usage['generation_tokens'] = tokens
            return result
        monkeypatch.setattr(rag, 'review_assessments', review)
        model = MissingUsageModel(); model.count = 1
        result = WorkspaceAgent(model).run(resumed)
    assert result['status'] == 'answered'
    assert len(seen) == 1
    assert result['usage']['model_calls'] == 4
    assert result['usage']['provider_usage_unknown'] is True
    assert set(result['usage']['generation_tokens'].values()) == {None}


def test_operation_ack_cannot_start_provider_after_active_deadline(monkeypatch):
    # Advance a synthetic monotonic clock during durable ACK, rather than sleep
    # or rely on a timing race. The journal outcome stays pending conservatively.
    clock = [1000.0]
    monkeypatch.setattr(workspace_agent.time, 'monotonic', lambda: clock[0])
    class SlowAcknowledgement(Journal):
        def operation(self, phase, *, step, timeout):
            super().operation(phase, step=step, timeout=timeout)
            clock[0] += 76
    journal = SlowAcknowledgement()
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    model = DecisionModel()
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_timeout'
    assert result['agent']['state'] == 'timeout'
    assert result['usage']['budget_exhausted'] is True
    assert result['usage']['model_calls'] == 0
    assert model.count == 0
    assert len(journal.boundaries) == 1


def test_operation_ack_preserves_final_review_reserve(monkeypatch):
    from lablens.model import HTTPModel
    clock = [1000.0]
    monkeypatch.setattr(workspace_agent.time, 'monotonic', lambda: clock[0])
    class SlowAcknowledgement(Journal):
        def operation(self, phase, *, step, timeout):
            super().operation(phase, step=step, timeout=timeout)
            clock[0] += 56  # total 75s; the final 20s belongs to review
    class ScriptedHTTPModel(HTTPModel):
        def __init__(self):
            self.base = 'http://127.0.0.1:1/v1'
            self.model = 'scripted-no-provider'
            self.timeout_seconds = 45
            self.count = 0
        def choose_workspace_action(self, payload, *, timeout_seconds):
            self.count += 1
            pytest.fail('Decision must not spend the final review reservation')
    journal = SlowAcknowledgement()
    monkeypatch.setattr(workspace_agent, 'CheckpointJournal', lambda: journal)
    model = ScriptedHTTPModel()
    result = WorkspaceAgent(model).run(request())
    assert result['status'] == 'agent_timeout'
    assert result['usage']['model_calls'] == 0
    assert model.count == 0


def test_unavailable_and_invalid_memory_helpers_label_resume_usage_scope():
    from lablens.workspace_agent import unavailable_agent_result, invalid_memory_agent_result
    req = request().model_copy(update={'resume_context': ResumeContext(parent_task_id=str(uuid4()),
        checkpoint_revision=1, state_sha256='0' * 64, state={'payload_json': '{}'})})
    for result in (unavailable_agent_result(req), invalid_memory_agent_result(req, 'expired fixture')):
        assert result['usage_scope'] == 'current_execution_only_before_checkpoint_restore'
        assert result['usage']['model_calls'] == 0
        assert 'recovery' not in result
