"""Fail-closed tool-boundary recovery journal for the authenticated JVM worker hop.

The JVM owns durability, fencing and resume admission. A pending operation never
falls back to the preceding boundary. This module only resumes a complete batch;
provider requests are never retried here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from pydantic import Field

from .bounded_http import post as bounded_post
from .cancellation import current_task_id
from .schemas import StrictModel
from .service_auth import service_headers, ServiceAuthConfigError

SCHEMA = 'workspace-checkpoint-v1'
MAX_BYTES = 512 * 1024
_owner = ContextVar('lablens_execution_owner', default=None)


class CheckpointError(RuntimeError):
    def __init__(self, message, status='agent_checkpoint_unavailable'):
        super().__init__(message)
        self.status = status


class CheckpointState(StrictModel):
    payload_json: str = Field(min_length=2, max_length=MAX_BYTES)


class ResumeContext(StrictModel):
    parent_task_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    checkpoint_revision: int = Field(ge=1)
    state_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: CheckpointState


@contextmanager
def execution_owner(value):
    token = _owner.set(value)
    try:
        yield
    finally:
        _owner.reset(token)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def fingerprint(request, model, *, seconds, max_steps, max_calls, window, engine_sha):
    request_hash = digest(request.model_dump(exclude={'resume_context'}))
    # Credentials are never serialized, persisted or fingerprinted. Changes to
    # executable policy and model/context budgets invalidate a saved boundary.
    policy = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(Path(__file__).parent.glob('*.py'))}
    settings = {key: os.getenv(key, '') for key in (
        'LLM_BASE_URL', 'LLM_MODEL', 'LLM_TIMEOUT_SECONDS',
        'RAG_TIMEOUT_SECONDS', 'RAG_TOTAL_SECONDS', 'EMBEDDING_BASE_URL',
        'EMBEDDING_MODEL', 'EMBEDDING_MODEL_REVISION', 'EMBEDDING_BATCH_SIZE',
        'LABLENS_INDEX_DB', 'LABLENS_INDEX_MAX_ENTRIES', 'LABLENS_AGENT_TOOL_BASE_URL')}
    config = dict(engine_sha=engine_sha, policy=policy, settings=settings,
        model_type=type(model).__module__ + '.' + type(model).__qualname__,
        model=getattr(model, 'model', None), base=getattr(model, 'base', None),
        model_timeout=getattr(model, 'timeout_seconds', None), seconds=seconds,
        max_steps=max_steps, max_calls=max_calls, window=window)
    return request_hash, digest(config)


def _invalid():
    return CheckpointError('Saved checkpoint does not match the immutable request, execution configuration or complete tool boundary; no provider request was sent.', 'agent_invalid_checkpoint')


def load_state(resume, *, request_hash, config_hash, known, max_steps, max_calls, seconds):
    """Validate everything restored into execution, not merely its stored hash."""
    try:
        encoded = resume.state.payload_json.encode('utf-8')
        if len(encoded) > MAX_BYTES or hashlib.sha256(encoded).hexdigest() != resume.state_sha256:
            raise ValueError
        def no_duplicates(pairs):
            value = dict(pairs)
            if len(value) != len(pairs):
                raise ValueError
            return value
        state = json.loads(encoded, object_pairs_hook=no_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        expected = {'schema', 'request_sha256', 'config_sha256', 'next_step', 'elapsed_ms',
                    'result', 'observed', 'verified', 'exchanges', 'call_ids', 'invalid_submissions',
                    'lookups', 'context_calls', 'telemetry_stage_count', 'telemetry_request_count'}
        if not isinstance(state, dict) or set(state) != expected or state['schema'] != SCHEMA:
            raise ValueError
        if state['request_sha256'] != request_hash or state['config_sha256'] != config_hash:
            raise ValueError
        def integer(value, lower, upper):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError
            return value
        step = integer(state['next_step'], 1, max_steps + 1)
        integer(state['elapsed_ms'], 0, int(seconds * 1000))
        for key in ('telemetry_stage_count', 'telemetry_request_count'):
            integer(state[key], 0, 100)
        result, observed, verified = state['result'], state['observed'], state['verified']
        if not isinstance(result, dict) or not isinstance(observed, dict) or not isinstance(verified, dict):
            raise ValueError
        if len(observed) > len(known) or not set(verified) <= set(observed):
            raise ValueError
        for cid, hit in observed.items():
            if cid not in known or not isinstance(hit, dict) or any(hit.get(k) != v for k, v in known[cid].items()):
                raise ValueError
        if any(verified[cid] != observed[cid] for cid in verified):
            raise ValueError
        agent, usage, retrieval = result['agent'], result['usage'], result['retrieval']
        if result['mode'] != 'agent' or result['status'] != 'agent_budget_exceeded' or agent['state'] != 'running':
            raise ValueError
        if integer(agent['steps'], 0, max_steps) != step - 1 or usage['model_calls'] != step - 1:
            raise ValueError
        tool_count = integer(agent['tool_calls'], 0, max_calls)
        if type(usage['provider_usage_unknown']) is not bool or usage['budget_exhausted'] is not False:
            raise ValueError
        integer(usage['model_calls'], 0, max_steps)
        def usage_number(number):
            if number is None and usage['provider_usage_unknown']:
                return
            integer(number, 0, 2**53 - 1)
        for key in ('embedding_requests', 'embedded_inputs', 'embedding_cache_hits', 'embedding_tokens'):
            usage_number(usage[key])
        if set(usage['generation_tokens']) != {'prompt_tokens', 'completion_tokens', 'total_tokens'}:
            raise ValueError
        for number in usage['generation_tokens'].values():
            usage_number(number)
        if not isinstance(agent['trace'], list) or len(agent['trace']) > 2000:
            raise ValueError
        if not isinstance(state['context_calls'], list) or len(state['context_calls']) != step - 1:
            raise ValueError
        searches = retrieval['searches']
        if not isinstance(searches, list) or integer(state['lookups'], 0, max_calls) != len(searches):
            raise ValueError
        searched = set()
        for search in searches:
            if not isinstance(search, dict) or not isinstance(search['chunk_ids'], list):
                raise ValueError
            if search['snapshot_id'] != retrieval['snapshot_id'] or search['documents_sha256'] != retrieval['documents_sha256']:
                raise ValueError
            searched.update(search['chunk_ids'])
        if searched != set(observed):
            raise ValueError
        exchanges = state['exchanges']
        if not isinstance(exchanges, list) or len(exchanges) > max_steps + max_calls:
            raise ValueError
        ids, batches, position = [], 0, 0
        while position < len(exchanges):
            assistant = exchanges[position]
            if assistant['role'] != 'assistant' or not isinstance(assistant['tool_calls'], list) or not assistant['tool_calls']:
                raise ValueError
            batches += 1
            position += 1
            for call in assistant['tool_calls']:
                cid = call['id']
                if not isinstance(cid, str) or not 1 <= len(cid) <= 200 or cid in ids:
                    raise ValueError
                if call['function']['name'] not in {'search_workspace', 'verify_sources', 'read_source_passages', 'submit_report', 'verify_and_submit_report'}:
                    raise ValueError
                json.loads(call['function']['arguments'])
                tool = exchanges[position]
                if tool['role'] != 'tool' or tool['tool_call_id'] != cid or not isinstance(json.loads(tool['content']), dict):
                    raise ValueError
                ids.append(cid)
                position += 1
        if batches != step - 1 or len(ids) != tool_count or sorted(ids) != state['call_ids']:
            raise ValueError
        if not isinstance(state['invalid_submissions'], list) or len(state['invalid_submissions']) > max_calls or any(
                not isinstance(value, str) or len(value) > 30000 for value in state['invalid_submissions']):
            raise ValueError
        if step == 1 and (tool_count or observed or verified or searches):
            raise ValueError
        return state
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, AttributeError, RecursionError):
        raise _invalid() from None


class CheckpointJournal:
    def __init__(self):
        self.owner, self.task_id, self.revision = _owner.get(), current_task_id(), 0
        self.enabled = self.owner is not None
        self.base = None
        if self.enabled:
            try:
                UUID(self.owner)
                UUID(self.task_id)
                value = os.getenv('LABLENS_CHECKPOINT_BASE_URL', '').rstrip('/')
                parsed = urlparse(value)
                if (parsed.scheme != 'http' or not ipaddress.ip_address(parsed.hostname).is_loopback
                        or not parsed.port or parsed.username or parsed.password or parsed.path
                        or parsed.query or parsed.fragment):
                    raise ValueError
                self.base = value
                identity = service_headers()
                if 'X-LabLens-Service-Key' not in identity:
                    raise ValueError
                self.headers = {**identity, 'X-LabLens-Execution-Owner': self.owner}
            except (ValueError, TypeError, AttributeError, ServiceAuthConfigError):
                raise CheckpointError('Checkpoint service identity or fixed loopback endpoint is not configured; no provider request was sent.') from None

    def _write(self, kind, *, state=None, operation_id=None, timeout):
        if not self.enabled:
            return
        if timeout <= 0:
            raise CheckpointError('No execution time remains for durable checkpoint acknowledgement; no next provider request was sent.')
        body = dict(revision=self.revision + 1, kind=kind, operation_id=operation_id, state=state)
        if state is not None:
            body['state_sha256'] = hashlib.sha256(state['payload_json'].encode('utf-8')).hexdigest()
        else:
            body['state_sha256'] = None
        try:
            response = bounded_post(self.base + '/api/internal/tasks/' + self.task_id + '/checkpoint',
                json=body, headers=self.headers, timeout=min(3.0, timeout), follow_redirects=False, trust_env=False)
            response.raise_for_status()
            receipt = response.json()
            if not isinstance(receipt, dict) or receipt.get('revision') != body['revision']:
                raise ValueError
            if state is not None and receipt.get('state_sha256') != body['state_sha256']:
                raise ValueError
        except (httpx.HTTPError, ValueError, TypeError):
            raise CheckpointError('Durable checkpoint acknowledgement was unavailable; execution stopped without retry or another provider request.') from None
        self.revision += 1

    def boundary(self, state, *, timeout):
        if not self.enabled:
            return
        try:
            payload = canonical(state)
            if len(payload.encode('utf-8')) > MAX_BYTES:
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            raise CheckpointError('Complete checkpoint exceeds the bounded state format; execution stopped before another provider request.') from None
        self._write('boundary', state={'payload_json': payload}, timeout=timeout)

    def operation(self, phase, *, step, timeout):
        self._write('operation_started', operation_id=f'{phase}-{step}-{uuid4().hex}', timeout=timeout)
