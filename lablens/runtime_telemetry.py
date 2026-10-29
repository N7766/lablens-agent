"""Bounded, payload-free timing for one synchronous workspace execution.

Stages can contain requests and other stages, so durations must not be summed.
Body byte counts use the installed HTTPX JSON encoder, not a token estimator.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from .cancellation import ExecutionCancelled

VERSION = 'runtime-stages-v1'
_CURRENT = ContextVar('lablens_runtime_telemetry', default=None)
TOKEN_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def safe_usage(value):
    value = value if isinstance(value, dict) else {}
    return {key: value.get(key) if type(value.get(key)) is int and value[key] >= 0 else None
            for key in TOKEN_KEYS}


def request_body_bytes(payload):
    # This mirrors json=payload in HTTPX; headers and credentials never enter it.
    return len(httpx.Request('POST', 'http://127.0.0.1/', json=payload).content)


def _outcome(exc):
    if isinstance(exc, ExecutionCancelled):
        return 'cancelled'
    if isinstance(exc, httpx.TimeoutException):
        return 'timeout'
    if isinstance(exc, httpx.HTTPStatusError):
        return 'http_error'
    if isinstance(exc, httpx.HTTPError):
        return 'transport_error'
    return 'error'


@dataclass
class Stage:
    id: str
    phase: str
    step: int
    started: float
    outcome: str = 'returned'
    fields: dict | None = None
    start_event: dict | None = None

    def request_details(self, payload, timeout_seconds):
        details = dict(request_body_bytes=request_body_bytes(payload), timeout_ms=round(timeout_seconds*1000, 3))
        self.fields.update(details)
        self.start_event.update(details)


class ExecutionTelemetry:
    def __init__(self, events, *, started, deadline, clock):
        self.events, self.started, self.deadline, self.clock = events, started, deadline, clock
        self.stages = []
        self.stage_count = self.request_count = 0

    def timing(self):
        now = self.clock()
        return dict(elapsed_ms=round(max(0, now-self.started)*1000, 3),
                    remaining_ms=round(max(0, self.deadline-now)*1000, 3))

    @contextmanager
    def bind(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    @contextmanager
    def stage(self, phase, *, step, name=None):
        self.stage_count += 1
        span = Stage('s'+str(self.stage_count), phase, step, self.clock())
        fields = dict(stage_id=span.id, phase=phase, step=step,
                      parent_stage_id=self.stages[-1].id if self.stages else None)
        if name is not None:
            fields['name'] = name
        span.fields = fields
        span.start_event = dict(event='stage_started', **fields, **self.timing())
        self.events.append(span.start_event)
        self.stages.append(span)
        try:
            yield span
        except BaseException as exc:
            span.outcome = _outcome(exc)
            raise
        finally:
            self.stages.pop()
            self.events.append(dict(event='stage_finished', **fields, **self.timing(),
                duration_ms=round(max(0, self.clock()-span.started)*1000, 3), outcome=span.outcome))

    def stopped(self, status):
        outcome = ('cancelled' if status == 'agent_cancelled' else 'timeout' if status == 'agent_timeout'
                   else 'rejected' if status in {'agent_protocol_error', 'context_budget_exceeded', 'agent_budget_exceeded'}
                   else 'error')
        for span in self.stages:
            if span.outcome == 'returned':
                span.outcome = outcome


class RequestObservation:
    def __init__(self, telemetry, payload, timeout_seconds, transport):
        self.telemetry = telemetry
        self.started = telemetry.clock() if telemetry else None
        self.response_received = False
        self.http_status = None
        self.usage = safe_usage(None)
        if telemetry:
            telemetry.request_count += 1
            stage = telemetry.stages[-1] if telemetry.stages else None
            self.fields = dict(request_id='r'+str(telemetry.request_count),
                stage_id=stage.id if stage else None, phase=stage.phase if stage else 'unknown',
                step=stage.step if stage else None, transport=transport,
                request_body_bytes=request_body_bytes(payload), timeout_ms=round(timeout_seconds*1000, 3))
            telemetry.events.append(dict(event='request_started', **self.fields, **telemetry.timing()))

    def received(self, response, body=None):
        if self.telemetry is None:
            return
        self.response_received = True
        self.http_status = response.status_code
        if isinstance(body, dict):
            self.usage = safe_usage(body.get('usage'))

    def finish(self, outcome):
        if self.telemetry:
            t = self.telemetry
            t.events.append(dict(event='request_finished', **self.fields, **t.timing(),
                duration_ms=round(max(0, t.clock()-self.started)*1000, 3), outcome=outcome,
                response_received=self.response_received, http_status=self.http_status,
                usage=self.usage, usage_known=all(value is not None for value in self.usage.values()),
                provider_acceptance='unknown' if not self.response_received else 'response_received'))


@contextmanager
def observe_request(payload, timeout_seconds, *, transport='provider_http'):
    observation = RequestObservation(_CURRENT.get(), payload, timeout_seconds, transport)
    try:
        yield observation
    except BaseException as exc:
        observation.finish(_outcome(exc))
        raise
    else:
        observation.finish('returned')
