from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.cancellation import (CancellationRegistry, ExecutionCancelled,
                                  ExecutionConflict, RegistryFull, check_cancelled)


def test_pending_worker_is_not_acknowledged_until_stack_exits_and_next_call_stops():
    registry = CancellationRegistry()
    entered, release = Event(), Event()
    calls = []
    def work():
        with registry.execution('task') as execution:
            calls.append('in_flight_provider')
            entered.set()
            assert release.wait(3)
            execution.result = {'usage': {'model_calls': 1}}
            check_cancelled()
            calls.append('forbidden_followup_provider')
    with ThreadPoolExecutor() as pool:
        future = pool.submit(work)
        assert entered.wait(3)
        pending = registry.cancel('task')
        assert pending['stopped'] is False
        assert pending['usage'] is None
        release.set()
        with pytest.raises(ExecutionCancelled):
            future.result(3)
    acknowledged = registry.cancel('task')
    assert acknowledged['stopped'] is True
    assert acknowledged['usage']['model_calls'] == 1
    assert calls == ['in_flight_provider']


def test_early_tombstone_rejects_late_dispatch_and_bounded_registry_never_evicts_it():
    registry = CancellationRegistry(capacity=1)
    assert registry.cancel('cancelled')['stopped'] is True
    with pytest.raises(ExecutionCancelled), registry.execution('cancelled'):
        pytest.fail('Cancellation tombstone must prevent worker dispatch')
    with pytest.raises(RegistryFull), registry.execution('other'):
        pytest.fail('Registry capacity must not discard cancellation tombstones')
    with pytest.raises(ExecutionCancelled), registry.execution('cancelled'):
        pass


def test_completed_execution_is_not_replayed_and_context_is_reset():
    registry = CancellationRegistry()
    with registry.execution('once'):
        check_cancelled()
    with pytest.raises(ExecutionConflict), registry.execution('once'):
        pass
    registry.cancel('once')
    check_cancelled()  # Cancellation from another execution must not leak.


def test_child_lease_prevents_false_ack_after_parent_timeout_and_rejects_late_children():
    registry = CancellationRegistry()
    entered, release = Event(), Event()
    def child():
        with registry.child_execution('root'):
            entered.set()
            assert release.wait(3)
            check_cancelled()
    with ThreadPoolExecutor() as pool:
        with registry.execution('root'):
            future = pool.submit(child)
            assert entered.wait(3)
        # Simulate a parent stack that has returned after its child HTTP timeout.
        assert registry.cancel('root')['stopped'] is False
        with pytest.raises(ExecutionConflict), registry.child_execution('root'):
            pass
        release.set()
        with pytest.raises(ExecutionCancelled):
            future.result(3)
    assert registry.cancel('root')['stopped'] is True


def test_cancel_endpoint_prevents_late_model_construction_and_rejects_non_uuid(monkeypatch):
    import lablens.rag_api as api
    monkeypatch.setattr(api, 'registry', CancellationRegistry())
    def unexpected():
        pytest.fail('A pre-cancelled request must not construct a model client')
    monkeypatch.setattr(api, 'HTTPModel', unexpected)
    client = TestClient(app)
    task_id = str(uuid4())
    response = client.post(f'/api/rag/executions/{task_id}/cancel')
    assert response.status_code == 200
    assert response.json()['stopped'] is True
    body = {'question': 'q', 'mode': 'agent', 'strategy': 'bm25', 'snapshot_id': 'w:1',
            'documents': [{'id': 'source', 'title': 'Source', 'text': 'Evidence', 'source': 'test://source'}]}
    response = client.post('/api/rag/answer', json=body, headers={'X-LabLens-Task-Id': task_id})
    assert response.status_code == 409
    assert response.json()['detail']['status'] == 'agent_cancelled'
    assert client.post('/api/rag/executions/not-a-task/cancel').status_code == 422


def test_control_endpoint_preserves_result_usage_without_replaying_worker(monkeypatch):
    import lablens.rag_api as api
    monkeypatch.setattr(api, 'registry', CancellationRegistry())
    calls = []
    def answer(request, budget):
        calls.append(request.question)
        return {'status': 'answered', 'usage': {'model_calls': 1}}
    monkeypatch.setattr(api, '_answer', answer)
    client = TestClient(app)
    task_id = str(uuid4())
    body = {'question': 'q', 'mode': 'extractive', 'strategy': 'bm25', 'snapshot_id': 'w:1',
            'documents': [{'id': 'source', 'title': 'Source', 'text': 'Evidence', 'source': 'test://source'}]}
    assert client.post('/api/rag/answer', json=body, headers={'X-LabLens-Task-Id': task_id}).status_code == 200
    assert client.post('/api/rag/answer', json=body, headers={'X-LabLens-Task-Id': task_id}).status_code == 409
    ack = client.post(f'/api/rag/executions/{task_id}/cancel').json()
    assert ack['stopped'] is True and ack['usage'] == {'model_calls': 1}
    assert calls == ['q']
