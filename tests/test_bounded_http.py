"""Actual async transport cancellation and local sockets, no paid model traffic."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from threading import Event
import time

import httpx
import pytest

from lablens import bounded_http
from lablens.cancellation import CancellationRegistry, ExecutionCancelled, on_cancellation, check_cancelled


@pytest.fixture(scope='module')
def deadline_probe():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify_http_deadlines_round17.py'
    spec = importlib.util.spec_from_file_location('deadline_probe_round17', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_socket_deadlines_cancel_and_success(deadline_probe):
    results = deadline_probe.run_probes()
    assert deadline_probe.validate(results)['status'] == 'passed'
    import json
    serialized = json.dumps(results)
    assert 'Authorization' not in serialized and 'Bearer ' not in serialized
    assert 'local-fixture-placeholder' not in serialized and 'messages' not in serialized
    assert 'round17-fixed-synthetic-service-identity-only' not in serialized


def test_new_probe_cannot_overwrite_prior_evidence(deadline_probe, tmp_path):
    with pytest.raises(FileExistsError):
        deadline_probe.main(['--output', str(tmp_path)])


def test_callback_registration_after_cancel_and_unsubscribe_do_not_miss_or_leak():
    registry, calls = CancellationRegistry(), []
    with registry.execution('first') as execution:
        registry.cancel('first')
        with on_cancellation(lambda: calls.append('late')):
            assert calls == ['late']
        assert execution.callbacks == {}
    with registry.execution('second') as execution:
        with on_cancellation(lambda: calls.append('unsubscribed')):
            assert len(execution.callbacks) == 1
        registry.cancel('second')
        assert calls == ['late'] and execution.callbacks == {}


def test_cancel_tears_down_async_stream_client_and_task_before_ack(monkeypatch):
    registry = CancellationRegistry()
    entered, closing, release_close, stream_closed, transport_closed = [Event() for _ in range(5)]
    request_tasks = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.sleep(10)
            yield b'{}'

        async def aclose(self):
            stream_closed.set()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            request_tasks.append(asyncio.current_task())
            return httpx.Response(200, stream=Stream(), request=request)

        async def aclose(self):
            closing.set()
            while not release_close.is_set():
                await asyncio.sleep(.005)
            transport_closed.set()

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=Transport(), **kw))

    def run():
        with registry.execution('task'):
            bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=.15)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert entered.wait(2)
        assert registry.cancel('task')['stopped'] is False
        assert closing.wait(2)
        # A repeated cancel must not interrupt cleanup or claim a false stop.
        assert registry.cancel('task')['stopped'] is False
        assert stream_closed.is_set() and not future.done()
        time.sleep(.2)  # the original deadline cannot interrupt required cleanup
        assert not future.done() and not transport_closed.is_set()
        release_close.set()
        with pytest.raises(ExecutionCancelled):
            future.result(2)
    assert transport_closed.is_set() and all(task.done() for task in request_tasks)
    assert registry.cancel('task')['stopped'] is True
    assert registry._entries['task'].callbacks == {}


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_invalid_deadline_never_constructs_transport(monkeypatch, timeout):
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: pytest.fail('Unexpected transport'))
    with pytest.raises(ValueError):
        bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=timeout)


def test_sync_api_rejects_nested_event_loop_without_background_work():
    async def run():
        with pytest.raises(RuntimeError, match='synchronous worker thread'):
            bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=1)
    asyncio.run(run())


def test_deadline_cancellation_race_keeps_cancel_as_terminal(monkeypatch):
    registry = CancellationRegistry()
    original = httpx.AsyncClient
    async def handler(request):
        registry.cancel('race')
        await asyncio.sleep(1)
        return httpx.Response(200, json={})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with registry.execution('race'), pytest.raises(ExecutionCancelled):
        bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=.1)
    assert registry.cancel('race')['stopped'] is True


def test_os_executor_cleanup_is_waited_for_not_falsely_acknowledged(monkeypatch):
    """Model the non-preemptible resolver caveat, without resolving any hostname."""
    registry, entered, release = CancellationRegistry(), Event(), Event()
    original = httpx.AsyncClient

    def os_work():
        entered.set()
        assert release.wait(2)

    async def handler(request):
        await asyncio.get_running_loop().run_in_executor(None, os_work)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    def run():
        with registry.execution('resolver'):
            bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=.1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert entered.wait(2)
        time.sleep(.15)
        assert not future.done()  # timeout has fired, but OS work is still owned
        assert registry.cancel('resolver')['stopped'] is False
        release.set()
        with pytest.raises(bounded_http.HTTPDeadlineExceeded):
            future.result(2)
    assert registry.cancel('resolver')['stopped'] is True


def test_late_snapshotted_cancel_cannot_interrupt_client_cleanup(monkeypatch):
    registry, closing, release, closed = CancellationRegistry(), Event(), Event(), Event()
    callbacks, observed_responses = [], []
    original = httpx.AsyncClient

    @contextmanager
    def delayed_delivery(callback):
        # Model cancel() taking a callback snapshot, then another thread delaying
        # its delivery until after unsubscribe. The callback itself is real.
        callbacks.append(callback)
        yield

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={}, request=request)

        async def aclose(self):
            closing.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            closed.set()

    monkeypatch.setattr(bounded_http, 'on_cancellation', delayed_delivery)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=Transport(), **kw))

    def run():
        with registry.execution('late'):
            observed_responses.append(bounded_http.post('http://127.0.0.1/', json={}, headers={}, timeout=1))
            check_cancelled()  # caller retains the complete response, but stops work

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert closing.wait(2)
        assert registry.cancel('late')['stopped'] is False
        callbacks[0]()  # captured before unsubscribe, delivered during aclose
        time.sleep(.03)
        assert not future.done() and not closed.is_set()
        release.set()
        with pytest.raises(ExecutionCancelled):
            future.result(2)
    assert closed.is_set() and len(observed_responses) == 1
    assert registry.cancel('late')['stopped'] is True
