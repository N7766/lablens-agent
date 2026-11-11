#!/usr/bin/env python3
"""Freeze or explicitly run three bounded private-model compatibility requests.

Default mode is freeze: no HTTP, no credentials, no deployment. Run accepts only
the frozen 127.0.0.1 SSH-tunnel endpoint and LABLENS_PRIVATE_MODEL_API_KEY from
the environment. It never retries, falls back, rents a GPU, or scores semantic
quality. An interrupted run directory is intentionally not resumable.
"""
import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import pydantic

from lablens.answer_quality import enforce_review_checks, make_review_contract
from lablens.assessment import (evidence_catalog, resolve_selections,
                               selection_schema, validate_assessments)
from lablens.model import workspace_action_payload
from lablens.proposition_scope import require_consistent_proposition_scopes
from lablens.rag import RAGDocument, _generation_payload, chunk_documents
from lablens.review_delta import restore_review_delta
from lablens.workspace_agent import ClarificationArguments, ReadArguments, SearchArguments

VERSION = 'private-model-compatibility-v2'
DEFAULT_REVIEW_PROTOCOL = 'scoped-review-delta-v3'
DECISION_REVIEW_PROTOCOL = 'scoped-review-decisions-v4'
REVIEW_PROTOCOLS = (DEFAULT_REVIEW_PROTOCOL, DECISION_REVIEW_PROTOCOL)
KEY_ENV = 'LABLENS_PRIVATE_MODEL_API_KEY'
MAX_BODY_BYTES = 128 * 1024
MAX_REQUEST_SECONDS = 40
MAX_TOTAL_SECONDS = 120
MAX_REQUESTS = 3
PROFILE_PATH = ROOT / 'deploy/private-model/profile.json'
SCOPE = ('Deployment compatibility only: Chinese output, structured Agent action, '
         'production scoped review contract. Not model quality, independent semantic '
         'acceptance, concurrency capacity, or production traffic evidence.')


class ProbeError(ValueError):
    """Only static, credential-free messages are raised across the CLI boundary."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_bytes(path, body):
    """Exclusive + fsync: intent must be durable before entering HTTP transport."""
    with Path(path).open('xb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2,
                                allow_nan=False).encode('utf-8') + b'\n')


def strict_json(raw):
    # Reject duplicate keys and non-finite values, including frozen-file edits.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProbeError('Duplicate JSON keys are not allowed.')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ProbeError('Non-finite JSON is not allowed.')))


def read_json(path):
    return strict_json(Path(path).read_bytes())


def validate_base_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or not port or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != '/v1'
                or value != f'http://127.0.0.1:{port}/v1'):
            raise ValueError
    except (ValueError, TypeError):
        raise ProbeError('Use exactly http://127.0.0.1:<port>/v1 through an SSH tunnel.') from None
    return value


def source_map():
    paths = sorted((ROOT / 'lablens').rglob('*.py'))
    paths.append(Path(__file__).resolve())
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in paths}


def deployment_profile(model):
    """Bind expected deployment identity, not a claim about the remote server."""
    try:
        raw = PROFILE_PATH.read_bytes()
        value = strict_json(raw)
        identity_fields = ('schema_version', 'model_id', 'model_revision', 'tokenizer_revision',
                           'served_model_name', 'vllm_version', 'image_tag', 'image_digest',
                           'image_config_digest', 'platform', 'dtype', 'max_model_len',
                           'chat_template_utf8_sha256', 'weight_files_expected_from_hf_metadata')
        identity = {key: value[key] for key in identity_fields}
        if (identity['schema_version'] != 'lablens-private-model-v1'
                or identity['served_model_name'] != model
                or not re.fullmatch(r'[a-f0-9]{40}', identity['model_revision'])
                or not re.fullmatch(r'[a-f0-9]{40}', identity['tokenizer_revision'])
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', identity['image_digest'])):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ProbeError('Deployment profile is missing, invalid, or does not match the requested model alias.') from None
    return dict(path='deploy/private-model/profile.json', file_sha256=sha(raw),
                expected_identity=identity, server_identity_verified=False,
                scope='Expected pinned deployment identity only; HTTP responses do not authenticate loaded weights.')


def fixture():
    """Small explicitly synthetic corpus; no user or workspace data is exported."""
    question = '核对资料中的 SFT 基线，并区分配置记录与历史运行证据。'
    hits = chunk_documents([
        RAGDocument(id='synthetic-baseline', title='Synthetic compatibility data',
                    source='synthetic/results.md', text='本表仅为部署兼容性测试数据。\nSFT 基线：准确率 86%。'),
        RAGDocument(id='synthetic-config', title='Synthetic configuration',
                    source='synthetic/config.yaml', text='cache_enabled: true\n# 没有提供对应历史运行日志。'),
    ])
    passages = evidence_catalog(hits)
    baseline = next(key for key, value in passages.items() if 'SFT 基线' in value['quote'])
    draft = resolve_selections({'assessments': [
        dict(statement='资料包含 SFT 基线。', verdict='supported', basis='documented_fact',
             explanation='合成资料明确列出了 SFT 基线。', evidence_ids=[baseline]),
        dict(statement='配置足以证明历史运行启用了缓存。', verdict='insufficient', basis='historical_observation',
             explanation='没有对应历史运行日志；配置记录不能证明历史执行。', evidence_ids=[]),
    ]}, hits)
    return dict(question=question, hits=hits, draft=draft,
                context={'scoped_review_contract': make_review_contract(question, draft)})


def validate_review_protocol(value):
    if type(value) is not str or value not in REVIEW_PROTOCOLS:
        raise ProbeError('Select an exact supported review protocol before freezing.')
    return value


def review_payload_protocol(payload):
    """Decode only the frozen request policy, never guess from model output."""
    data = strict_json(payload['messages'][1]['content'])
    present = [key for key in ('server_review_delta', 'server_review_decisions') if key in data]
    if len(present) != 1:
        raise ProbeError('Frozen review payload requires exactly one explicit protocol policy.')
    key = present[0]
    policy = data[key]
    version = DEFAULT_REVIEW_PROTOCOL if key == 'server_review_delta' else DECISION_REVIEW_PROTOCOL
    if type(policy) is not dict or policy.get('version') != version:
        raise ProbeError('Frozen review payload policy version does not match its protocol.')
    return version, policy


def payloads(model, review_protocol=DEFAULT_REVIEW_PROTOCOL):
    validate_review_protocol(review_protocol)
    data = fixture()
    search = SearchArguments.model_json_schema()
    search['properties']['strategy']['enum'] = ['bm25']
    search['properties']['top_k']['maximum'] = 3
    tools = [dict(type='function', function=dict(name=name, description=description, parameters=schema))
             for name, description, schema in [
                 ('search_workspace', 'Search the immutable workspace snapshot. Factual questions must search first.', search),
                 ('read_source_passages', 'Read already retrieved source passages only.', ReadArguments.model_json_schema()),
                 ('request_clarification', 'Clarify an ambiguous user request.', ClarificationArguments.model_json_schema()),
                 ('verify_and_submit_report', 'Verify all retrieved chunks and submit an evidence report.', selection_schema([])),
             ]]
    action = workspace_action_payload([
        {'role': 'system', 'content': '你是 LabLens Agent。资料尚未检索。必须首先调用 search_workspace，选择 bm25，top_k 为 3，查询用户问题；不能猜测资料内容。'},
        {'role': 'user', 'content': data['question']},
    ], tools, model=model)
    review = _generation_payload(data['question'], data['hits'],
                                 {'model': model, 'review_protocol': review_protocol},
                                 draft=data['draft'], context_data=data['context'])
    if review_payload_protocol(review)[0] != review_protocol:
        raise ProbeError('Production review payload differs from the selected protocol; no request was sent.')
    plain = dict(model=model, temperature=0, max_tokens=64, messages=[
        dict(role='system', content='这是中文接口连通性检查。请严格遵循用户要求，只输出指定文字。'),
        dict(role='user', content='只回答：本地模型就绪'),
    ])
    return [dict(name=name, payload=payload, payload_sha256=sha(canonical(payload)))
            for name, payload in [('chinese', plain), ('workspace_action', action), ('scoped_review', review)]]


def build_plan(base_url, model, request_seconds=40, total_seconds=120,
               review_protocol=DEFAULT_REVIEW_PROTOCOL):
    validate_base_url(base_url)
    validate_review_protocol(review_protocol)
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}', model):
        raise ProbeError('Invalid model alias.')
    if (type(request_seconds) not in (int, float) or not math.isfinite(request_seconds)
            or not 0 < request_seconds <= MAX_REQUEST_SECONDS
            or type(total_seconds) not in (int, float) or not math.isfinite(total_seconds)
            or not 0 < total_seconds <= MAX_TOTAL_SECONDS):
        raise ProbeError('Request budget must be <=40 seconds and total budget <=120 seconds.')
    return dict(version=VERSION, base_url=base_url, model=model,
                review_protocol=review_protocol,
                requests=payloads(model, review_protocol), source_files=source_map(),
                deployment_profile=deployment_profile(model),
                runtime_versions=dict(python=platform.python_version(), httpx=httpx.__version__,
                                      pydantic=pydantic.__version__),
                max_requests=MAX_REQUESTS, request_seconds=request_seconds,
                total_seconds=total_seconds, max_body_bytes=MAX_BODY_BYTES,
                retries=0, redirects=False, trust_env=False,
                fixture_scope='synthetic_only', semantic_acceptance='not_evaluated',
                scope=SCOPE)


def freeze(output, *, base_url='http://127.0.0.1:18080/v1', model='lablens-private',
           request_seconds=40, total_seconds=120, review_protocol=DEFAULT_REVIEW_PROTOCOL):
    plan = build_plan(base_url, model, request_seconds, total_seconds, review_protocol)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'plan.json', plan)
    write_bytes(output / 'plan.sha256', (sha((output / 'plan.json').read_bytes()) + '\n').encode())
    return dict(status='frozen_no_http', planned_requests=MAX_REQUESTS,
                plan_sha256=sha((output / 'plan.json').read_bytes()),
                review_protocol=review_protocol, scope=SCOPE)


def load_verified_plan(output):
    """Validate and return the exact bytes read once, never a second disk read."""
    output = Path(output)
    try:
        raw = (output / 'plan.json').read_bytes()
        digest = sha(raw)
        if digest != (output / 'plan.sha256').read_text().strip():
            raise ProbeError('Frozen plan digest mismatch; no request was sent.')
        plan = strict_json(raw)
        expected = build_plan(plan['base_url'], plan['model'], plan['request_seconds'],
                              plan['total_seconds'], plan['review_protocol'])
        if plan != expected:
            raise ProbeError('Frozen source, payload, or policy drift; freeze a new candidate before running.')
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        raise ProbeError('Invalid frozen plan; no request was sent.') from None
    return plan, raw, digest


def verify_plan(output):
    return load_verified_plan(output)[0]


def disk_plan_state(output, verified_digest):
    """Diagnostic only; a changed/deleted path cannot replace the original seal."""
    try:
        observed = sha((Path(output) / 'plan.json').read_bytes())
    except OSError:
        return dict(disk_plan_changed=True, disk_plan_state='unavailable')
    return dict(disk_plan_changed=observed != verified_digest,
                disk_plan_state='unchanged' if observed == verified_digest else 'changed')


def sanitize(raw, key):
    """Save response text only, without headers or exception messages.

    The exact configured credential and its JSON-escaped representation are
    removed even when the server echoes them in otherwise non-JSON error prose.
    Common credential fields are also redacted. This is a synthetic-data probe,
    not a general-purpose personal-data scrubber.
    """
    text = raw.decode('utf-8', errors='replace')
    def redact_string(value):
        for secret in sorted({key, json.dumps(key)[1:-1]}, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        return value
    def redact_value(value, depth=0):
        if isinstance(value, str):
            cleaned = redact_string(value)
            # Chat completion content can itself contain a JSON document.
            # Inspect bounded nested encodings so a JSON-escaped key inside
            # assistant content cannot bypass the outer-body scrubber.
            if depth < 4:
                try:
                    nested = strict_json(cleaned)
                    replaced = redact_value(nested, depth + 1)
                    if nested != replaced:
                        return json.dumps(replaced, ensure_ascii=False)
                except (ValueError, TypeError):
                    pass
            return cleaned
        if isinstance(value, list):
            return [redact_value(item, depth) for item in value]
        if isinstance(value, dict):
            return {redact_string(name): ('[REDACTED]' if re.fullmatch(
                r'api[_-]?key|authorization|password|access[_-]?token', name, re.I)
                else redact_value(item, depth)) for name, item in value.items()}
        return value
    try:
        value = strict_json(text)
        cleaned = redact_value(value)
        if cleaned != value:
            text = json.dumps(cleaned, ensure_ascii=False)
    except (ValueError, TypeError):
        pass
    text = redact_string(text)
    text = re.sub(r'(?i)(Bearer\s+)[^\s\"\x27,;<>]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)("(?:api[_-]?key|authorization|password|access[_-]?token)"\s*:\s*)"(?:\\.|[^"\\])*"',
                  r'\1"[REDACTED]"', text)
    return text.encode('utf-8')


def transport(url, payload, key, seconds):
    """Wall-clock timeout covers connect + headers + trickling response body.

    Socket/client cleanup is awaited. OS scheduling and filesystem stalls are
    not a hard real-time guarantee; no further model request is sent after the
    total deadline. The server may continue computing after local cancellation.
    """
    observed = dict(status_code=None, body=b'', body_complete=False, failure=None)

    async def perform():
        try:
            async with asyncio.timeout(seconds):
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                             timeout=seconds) as client:
                    async with client.stream('POST', url, headers={'Authorization': 'Bearer ' + key,
                                                                  'Accept-Encoding': 'identity'},
                                             json=payload) as response:
                        observed['status_code'] = response.status_code
                        # Raw chunks avoid decompression bombs; vLLM is requested
                        # to return identity encoding. Any other encoding fails.
                        if response.headers.get('content-encoding', 'identity') != 'identity':
                            observed['failure'] = 'unsupported_content_encoding'
                            return
                        async for chunk in response.aiter_raw(chunk_size=8192):
                            available = MAX_BODY_BYTES - len(observed['body'])
                            observed['body'] += chunk[:available]
                            if len(chunk) > available:
                                observed['failure'] = 'response_body_limit'
                                return
                        observed['body_complete'] = True
        except (TimeoutError, httpx.TimeoutException):
            observed['failure'] = 'timeout_usage_unknown'
        except httpx.HTTPError:
            observed['failure'] = 'transport_error_usage_unknown'
    asyncio.run(perform())
    return observed


def usage_of(body):
    usage = body.get('usage') if isinstance(body, dict) else None
    fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    known = isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0 for key in fields)
    known = known and usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
    return dict(status='provider_reported' if known else 'unknown',
                **{key: usage[key] if known else None for key in fields})


def validate_response(name, body, payload):
    try:
        if (not isinstance(body, dict) or not isinstance(body.get('choices'), list)
                or len(body['choices']) != 1):
            return 'malformed_response', None
        choice = body['choices'][0]
        if not isinstance(choice, dict):
            return 'malformed_response', None
        if choice.get('finish_reason') != 'stop':
            return ('finish_length' if choice.get('finish_reason') == 'length' else 'finish_not_stop'), None
        message = choice.get('message')
        if (not isinstance(message, dict) or message.get('role') != 'assistant'
                or not isinstance(message.get('content'), str) or message.get('tool_calls')):
            return 'malformed_response', None
        content = message['content']
        if name == 'chinese':
            return ('passed' if content.strip() == '本地模型就绪' else 'chinese_contract_failed'), None
        # Strict JSON decoding; no extraction of a fenced block, repair, or fallback.
        parsed = strict_json(content)
        if name == 'workspace_action':
            if (type(parsed) is not dict or set(parsed) != {'decision'}
                    or type(parsed['decision']) is not dict
                    or set(parsed['decision']) != {'action', 'arguments'}
                    or parsed['decision']['action'] != 'search_workspace'):
                return 'action_contract_failed', None
            args = SearchArguments.model_validate(parsed['decision']['arguments'])
            if args.strategy != 'bm25' or args.top_k != 3 or not args.query.strip():
                return 'action_contract_failed', None
            return 'passed', {'selected_action': 'search_workspace', 'tool_executed': False}
        if name == 'scoped_review':
            data = fixture()
            protocol, policy = review_payload_protocol(payload)
            if protocol == DEFAULT_REVIEW_PROTOCOL:
                restored = restore_review_delta(parsed, data['draft'], data['hits'], policy)
            else:
                from lablens.review_decisions import restore_review_decisions
                restored = restore_review_decisions(parsed, data['draft'], data['hits'], policy,
                                                    data['context']['scoped_review_contract'])
            # Match production's checks on both the model's restored rows and
            # the exact final rows. Later validation can rewrite or add rows;
            # it must neither hide a scope error nor introduce an unchecked one.
            review_scope = require_consistent_proposition_scopes(restored, stage='review_result')
            checked, quality = enforce_review_checks(data['question'], data['draft'], restored, hits=data['hits'])
            report = validate_assessments(data['question'], checked, data['hits'])
            rendered_scope = require_consistent_proposition_scopes(report, stage='rendered_report')
            return 'passed', dict(assessment_count=len(report['assessments']),
                                  selected_evidence_checks=quality, review_protocol=protocol,
                                  proposition_scope=dict(review=review_scope, rendered=rendered_scope),
                                  semantic_acceptance='not_evaluated')
    except (KeyError, TypeError, ValueError, IndexError):
        return 'structured_contract_failed', None
    return 'unknown_probe', None


def run(output):
    output = Path(output)
    plan, frozen_bytes, frozen_digest = load_verified_plan(output)
    key = os.environ.get(KEY_ENV, '')
    if not 8 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ProbeError('Set LABLENS_PRIVATE_MODEL_API_KEY to a printable ASCII credential of 8..4096 characters.')
    directory = output / 'run'
    directory.mkdir(exist_ok=False)  # Atomic single-run claim, including after a crash.
    started = time.monotonic()
    deadline = started + plan['total_seconds']
    attempts = []
    status = 'compatibility_passed'
    fatal = None
    try:
        # Preserve the exact verified input even if the source path is replaced
        # or deleted during HTTP. All receipts bind this immutable byte string.
        write_bytes(directory / 'frozen-plan.json', frozen_bytes)
        for index, item in enumerate(plan['requests'], 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = 'total_deadline_exhausted'
                break
            seconds = min(plan['request_seconds'], remaining)
            attempt = dict(index=index, name=item['name'], status='intent_recorded_result_unknown',
                           usage=usage_of(None), outcome_unknown=True)
            path = directory / f'{index:02d}-{item["name"]}'
            path.mkdir()
            write_json(path / 'intent.json', dict(index=index, name=item['name'],
                endpoint=plan['base_url'] + '/chat/completions', payload_sha256=item['payload_sha256'],
                frozen_plan_sha256=frozen_digest,
                request_timeout_seconds=seconds, elapsed_seconds=time.monotonic() - started,
                state='intent_before_send', retry_permitted=False,
                note='Intent proves planned dispatch, not receipt or billing. A crash leaves outcome unknown.'))
            attempts.append(attempt)
            # Evidence fsync can consume time; recalculate before dispatch.
            seconds = min(seconds, deadline - time.monotonic())
            if seconds <= 0:
                attempt.update(status='total_deadline_before_send', outcome_unknown=False,
                               transport_entered=False)
                write_json(path / 'attempt.json', attempt)
                status = 'total_deadline_exhausted'
                break
            attempt['transport_entered'] = True
            response = transport(plan['base_url'] + '/chat/completions', item['payload'], key, seconds)
            safe_body = sanitize(response['body'], key)
            write_bytes(path / 'response.sanitized.txt', safe_body)
            attempt.update(status_code=response['status_code'], body_complete=response['body_complete'],
                           body_bytes_observed=len(response['body']), sanitized_body_sha256=sha(safe_body),
                           response_redacted=safe_body != response['body'])
            body = None
            if response['body_complete']:
                try:
                    body = strict_json(response['body'])
                except (ValueError, UnicodeError):
                    pass
                attempt['usage'] = usage_of(body)
            if response['failure']:
                attempt['status'] = response['failure']
            elif response['status_code'] != 200:
                attempt['status'] = 'http_error'
            elif time.monotonic() > deadline:
                attempt['status'] = 'total_deadline_exhausted'
            else:
                attempt['status'], detail = validate_response(item['name'], body, item['payload'])
                if detail is not None:
                    # Detail can contain generated explanation from production checks.
                    write_bytes(path / 'validation.json', sanitize(canonical(detail), key) + b'\n')
                if attempt['status'] == 'passed' and attempt['usage']['status'] == 'unknown':
                    attempt['status'] = 'usage_unknown'
                elif (attempt['status'] == 'passed'
                      and attempt['usage']['completion_tokens'] > item['payload']['max_tokens']):
                    attempt['status'] = 'provider_reported_output_budget_violation'
            attempt['outcome_unknown'] = not response['body_complete']
            write_json(path / 'attempt.json', attempt)
            if attempt['status'] != 'passed':
                status = 'blocked'
                break
    except BaseException as error:
        # Never publish str(error): transports or filesystem errors can include keys.
        status = 'interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'local_error'
        fatal = type(error).__name__
        raise
    finally:
        known = all(row['usage']['status'] == 'provider_reported' for row in attempts)
        summary = dict(version=VERSION, status=status, attempts=attempts,
                       review_protocol=plan['review_protocol'],
                       frozen_plan_sha256=frozen_digest,
                       expected_deployment_profile=plan['deployment_profile'],
                       dispatch_intents=len(attempts), planned_requests=MAX_REQUESTS,
                       skipped_requests=MAX_REQUESTS - len(attempts),
                       aggregate_usage={field: sum(row['usage'][field] for row in attempts) if known and attempts else None
                                        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                       usage_status='provider_reported' if known and attempts else 'unknown',
                       elapsed_seconds=round(time.monotonic() - started, 6), fatal_type=fatal,
                       no_automatic_retry=True, semantic_acceptance='not_evaluated', scope=SCOPE)
        summary['model_weights_identity_verified'] = False
        disk_state = disk_plan_state(output, frozen_digest)
        summary.update(disk_state)
        write_json(directory / 'summary.json', summary)
        inventory = {str(path.relative_to(directory)): sha(path.read_bytes())
                     for path in sorted(directory.rglob('*')) if path.is_file()}
        write_json(directory / 'manifest.json', dict(files=inventory,
            frozen_plan_sha256=frozen_digest, **disk_state,
            integrity_scope='Content integrity only; not a signed or tamper-proof audit.'))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('freeze', 'run'), nargs='?', default='freeze')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-url', default=None)
    parser.add_argument('--model', default=None)
    parser.add_argument('--review-protocol', choices=REVIEW_PROTOCOLS, default=None)
    parser.add_argument('--request-seconds', type=float, default=None)
    parser.add_argument('--total-seconds', type=float, default=None)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'run':
            if any(value is not None for value in (args.base_url, args.model, args.request_seconds,
                                                   args.total_seconds, args.review_protocol)):
                raise ProbeError('Run uses only the frozen plan; endpoint/model/budget/protocol overrides are forbidden.')
            result = run(args.output)
        else:
            result = freeze(args.output, base_url=args.base_url or 'http://127.0.0.1:18080/v1',
                            model=args.model or 'lablens-private',
                            review_protocol=args.review_protocol or DEFAULT_REVIEW_PROTOCOL,
                            request_seconds=40 if args.request_seconds is None else args.request_seconds,
                            total_seconds=120 if args.total_seconds is None else args.total_seconds)
        print(json.dumps({key: result[key] for key in ('status', 'scope')}, ensure_ascii=False))
        return 0 if result['status'] in {'frozen_no_http', 'compatibility_passed'} else 1
    except ProbeError as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print('Local preflight or evidence write failed; no automatic retry. Inspect the run directory.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
