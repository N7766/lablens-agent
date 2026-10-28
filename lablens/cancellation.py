"""Cooperative cancellation for one local worker process, keyed by JVM task UUID.

MySQL owns the durable intent. This registry only acknowledges an execution once
its Python stack has exited; it never claims to cancel an upstream HTTP request.
Cancelled/finished IDs remain tombstones until process exit, so delayed duplicate
dispatch cannot restart work. A bounded registry rejects new IDs when full rather
than evicting a cancellation tombstone and permitting an unsafe replay.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any


class ExecutionCancelled(Exception):
    pass


class ExecutionConflict(Exception):
    pass


class RegistryFull(Exception):
    pass


@dataclass
class Execution:
    cancelled: Event = field(default_factory=Event)
    active: int = 0
    root_active: bool = False
    started: bool = False
    task_id: str | None = None
    result: dict[str, Any] | None = None


_current: ContextVar[Execution | None] = ContextVar('lablens_execution', default=None)


def check_cancelled() -> None:
    execution = _current.get()
    if execution is not None and execution.cancelled.is_set():
        raise ExecutionCancelled('Cancellation requested; no further work will be started.')


def current_task_id() -> str | None:
    execution = _current.get()
    return execution.task_id if execution else None


class CancellationRegistry:
    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self._lock = Lock()
        self._entries: dict[str, Execution] = {}

    def _entry(self, task_id: str) -> Execution:
        if task_id not in self._entries:
            if len(self._entries) >= self.capacity:
                raise RegistryFull('Worker task registry is full; restart the local worker after active work ends.')
            self._entries[task_id] = Execution(task_id=task_id)
        return self._entries[task_id]

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entry(task_id)
            entry.cancelled.set()
            return {'task_id': task_id, 'cancel_requested': True,
                    'stopped': not entry.active, 'execution_observed': entry.started,
                    'usage': (entry.result or {}).get('usage'),
                    'scope': 'Local Python execution stopped only when stopped=true; upstream HTTP cannot be forcibly cancelled.'}

    @contextmanager
    def execution(self, task_id: str | None):
        if task_id is None:
            yield Execution()
            return
        with self._lock:
            entry = self._entry(task_id)
            if entry.cancelled.is_set():
                raise ExecutionCancelled('Task was cancelled before dispatch.')
            if entry.started:
                raise ExecutionConflict('This task has already been dispatched; it cannot be executed twice.')
            entry.started = entry.root_active = True
            entry.active += 1
        token = _current.set(entry)
        try:
            yield entry
        finally:
            _current.reset(token)
            with self._lock:
                entry.active -= 1
                entry.root_active = False

    @contextmanager
    def child_execution(self, task_id: str):
        """A loopback retrieval child shares the stop flag and holds its own lease.

        Even if the parent HTTP call times out and exits, acknowledgement waits
        until all already-started children unwind. Late children cannot start
        after the root exits or a cancellation tombstone is present.
        """
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or not entry.root_active:
                raise ExecutionConflict('Parent execution is not active in this local worker.')
            if entry.cancelled.is_set():
                raise ExecutionCancelled('Parent task cancellation was observed before child work.')
            entry.active += 1
        token = _current.set(entry)
        try:
            yield entry
        finally:
            _current.reset(token)
            with self._lock:
                entry.active -= 1


registry = CancellationRegistry()
