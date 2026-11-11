#!/usr/bin/env python3
"""Single-use, loopback-only 3 compatibility + 3 Agent request gate.

prepare never contacts an upstream. Each serve phase claims its directory once;
even a crash before its first request cannot restart that phase. Only a matching
successful compatibility probe receipt permits the separately started Agent phase.
Reservations are fsynced before HTTP and never refunded. This is a cooperative
experiment boundary: the worker must not know the upstream key or URL, and the
operator must not bypass the gate. It does not stop cloud billing or authenticate
an upstream image/model. Receipts contain hashes/counts, never credentials/prose.
"""
import argparse
import asyncio
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import stat
import threading
import time
from urllib.parse import urlsplit

import httpx

VERSION = 'private-model-budget-gateway-v1'
CALLER_KEY_ENV = 'LABLENS_PRIVATE_GATEWAY_KEY'
UPSTREAM_KEY_ENV = 'LABLENS_PRIVATE_MODEL_API_KEY'
MAX_BODY = 128 * 1024
PHASES = ('compat', 'agent')
KINDS = ('workspace_action', 'workspace_action', 'evidence_review_by_id')


class GatewayError(ValueError):
    """Static, credential-free errors only."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GatewayError('Duplicate JSON key.')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(GatewayError('Non-finite JSON.')))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise GatewayError('Invalid bounded JSON.') from None


def read_file(path, limit=2 * 1024 * 1024):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise GatewayError('Evidence must be a regular file.')
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise GatewayError('File exceeds limit.')
        return raw
    except OSError:
        raise GatewayError('Required regular evidence file unavailable.') from None


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exclusive(path, value):
    raw = canonical(value) + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def loopback_origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not port
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != '/v1' or value != f'http://127.0.0.1:{port}/v1'
                or port in {8081, 8765, 3307, 6380}):
            raise ValueError
    except (ValueError, TypeError):
        raise GatewayError('Use a fixed non-main 127.0.0.1 HTTP /v1 origin.') from None
    return value


def credential(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise GatewayError('Missing or invalid private credential.')
    return value


def prepare(directory, *, compatibility_plan, upstream, listen_port=18081,
            upstream_mode='ssh_tunnel', request_seconds=40, phase_seconds=300):
    loopback_origin(upstream)
    if type(listen_port) is not int or not 1024 <= listen_port <= 65535:
        raise GatewayError('Invalid listener port.')
    local = loopback_origin(f'http://127.0.0.1:{listen_port}/v1')
    if local == upstream or upstream_mode not in {'ssh_tunnel', 'loopback_fixture'}:
        raise GatewayError('Invalid isolated upstream mode.')
    for value, maximum in ((request_seconds, 40), (phase_seconds, 600)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
            raise GatewayError('Invalid bounded timeout.')
    raw = read_file(compatibility_plan)
    probe = strict_json(raw)
    try:
        rows = probe['requests']
        if (probe['base_url'] != local or probe['max_requests'] != 3 or len(rows) != 3
                or [row['name'] for row in rows] != ['chinese', 'workspace_action', 'scoped_review']
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}', probe['model'])
                or any(digest(canonical(row['payload'])) != row['payload_sha256'] for row in rows)):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise GatewayError('Compatibility plan is not the frozen three-request plan for this listener.') from None
    # A separately recorded digest prevents accidentally preparing a modified plan.
    if read_file(Path(compatibility_plan).with_name('plan.sha256'), 100).decode().strip() != digest(raw):
        raise GatewayError('Compatibility plan digest mismatch.')
    plan = dict(version=VERSION, upstream=upstream, listen_port=listen_port,
                upstream_mode=upstream_mode, model=probe['model'], request_seconds=request_seconds,
                phase_seconds=phase_seconds, max_body_bytes=MAX_BODY, limits={'compat': 3, 'agent': 3},
                compatibility_plan_sha256=digest(raw),
                compatibility_payloads=[dict(name=row['name'], sha256=row['payload_sha256']) for row in rows],
                agent_kinds=list(KINDS), script_sha256=digest(Path(__file__).read_bytes()))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    exclusive(directory / 'plan.json', plan)
    exclusive(directory / 'plan-integrity.json', {'sha256': digest(canonical(plan) + b'\n')})
    return dict(status='prepared_no_http', plan_sha256=digest(canonical(plan) + b'\n'))


class Session:
    """An exclusive phase lifetime, not a resumable counter."""
    def __init__(self, directory, phase, *, compatibility_summary=None):
        self.directory = Path(directory)
        if phase not in PHASES or self.directory.is_symlink():
            raise GatewayError('Invalid phase or directory.')
        self.phase = phase
        self.lock_fd = None
        self.closed = False
        self.stopping = False
        self.lock = threading.RLock()
        self.inflight = False
        self.blocked = False
        self.reserved = self.completed = 0
        try:
            self.lock_fd = os.open(self.directory / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            if not stat.S_ISREG(os.fstat(self.lock_fd).st_mode):
                raise GatewayError('Phase lock must be a regular file.')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = read_file(self.directory / 'plan.json')
            self.plan = strict_json(raw)
            self.plan_sha256 = digest(raw)
            expected = strict_json(read_file(self.directory / 'plan-integrity.json'))['sha256']
            if (self.plan_sha256 != expected or self.plan.get('version') != VERSION
                    or self.plan.get('limits') != {'compat': 3, 'agent': 3}
                    or self.plan.get('agent_kinds') != list(KINDS)
                    or self.plan.get('max_body_bytes') != MAX_BODY
                    or self.plan.get('script_sha256') != digest(Path(__file__).read_bytes())):
                raise GatewayError('Frozen gateway plan changed.')
            loopback_origin(self.plan['upstream'])
            # Check evidence before creating the single-use claim.
            if phase == 'compat':
                if compatibility_summary is not None or (self.directory / 'agent.started.json').exists():
                    raise GatewayError('Compatibility phase cannot follow Agent phase.')
            else:
                self._authorize_agent(compatibility_summary)
            exclusive(self.directory / f'{phase}.started.json',
                      dict(phase=phase, plan_sha256=self.plan_sha256, started_at=time.time()))
            self.ledger_path = self.directory / f'{phase}.jsonl'
            fd = os.open(self.ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            sync_directory(self.directory)
            self.deadline = time.monotonic() + self.plan['phase_seconds']
        except BaseException:
            self._unlock()
            raise

    def _unlock(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _authorize_agent(self, summary_path):
        if summary_path is None:
            raise GatewayError('Agent phase requires an explicit matching compatibility receipt.')
        closed = strict_json(read_file(self.directory / 'compat.closed.json'))
        rows_raw = read_file(self.directory / 'compat.jsonl')
        rows = [strict_json(line) for line in rows_raw.splitlines()]
        summary_raw = read_file(summary_path)
        summary = strict_json(summary_raw)
        reserved = [row for row in rows if row.get('event') == 'reserved']
        completed = [row for row in rows if row.get('event') == 'completed']
        try:
            if (closed['plan_sha256'] != self.plan_sha256 or closed['ledger_sha256'] != digest(rows_raw)
                    or closed['blocked'] or closed['reserved'] != 3 or closed['completed'] != 3
                    or closed['inflight'] or len(reserved) != 3 or len(completed) != 3
                    or any(row['event'] not in {'reserved', 'completed'} for row in rows)
                    or [row['slot'] for row in reserved] != [1, 2, 3]
                    or [row['slot'] for row in completed] != [1, 2, 3]
                    or [row['payload_sha256'] for row in reserved] != [row['sha256'] for row in self.plan['compatibility_payloads']]
                    or any(row['http_status'] != 200 or row['body_complete'] is not True for row in completed)
                    or summary['status'] != 'compatibility_passed' or summary['dispatch_intents'] != 3
                    or summary['planned_requests'] != 3 or summary['skipped_requests'] != 0
                    or summary['frozen_plan_sha256'] != self.plan['compatibility_plan_sha256']
                    or len(summary['attempts']) != 3
                    or [row['index'] for row in summary['attempts']] != [1, 2, 3]
                    or [row['name'] for row in summary['attempts']] != ['chinese', 'workspace_action', 'scoped_review']
                    or any(row['status'] != 'passed' or row['outcome_unknown'] is not False
                           or row['body_complete'] is not True or row['status_code'] != 200 for row in summary['attempts'])):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise GatewayError('Compatibility evidence is incomplete, failed, unknown, or mismatched.') from None
        # The external grader must refer to the exact bodies delivered by this gate.
        if any(attempt.get('response_redacted') is not False
               or attempt.get('sanitized_body_sha256') != receipt['response_sha256']
               for attempt, receipt in zip(summary['attempts'], completed)):
            raise GatewayError('Compatibility response hashes do not match gateway receipts.')
        exclusive(self.directory / 'agent.authorization.json',
                  dict(compatibility_summary_sha256=digest(summary_raw), plan_sha256=self.plan_sha256))

    def append(self, value):
        fd = os.open(self.ledger_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'ab') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise GatewayError('Ledger must be a regular file.')
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def health(self):
        with self.lock:
            return dict(phase=self.phase, reserved=self.reserved, completed=self.completed,
                        blocked=self.blocked, plan_sha256=self.plan_sha256,
                        upstream_mode=self.plan['upstream_mode'])

    def stop_accepting(self):
        """Fence admission before listener shutdown, retaining the ledger lock.

        A handler may already be reading an incomplete request body. It must not
        reserve new upstream work after stop has begun. This is separate from a
        failed/blocked phase: normal shutdown after three completed compatibility
        calls must still leave valid evidence for the explicit Agent transition.
        """
        with self.lock:
            self.stopping = True

    def reserve(self, payload):
        with self.lock:
            if self.closed or self.stopping or self.blocked:
                raise GatewayError('Phase is closed or blocked.')
            if self.inflight:
                raise GatewayError('Only one upstream request may be in flight.')
            if self.reserved >= 3 or time.monotonic() >= self.deadline:
                self.blocked = True
                self.append(dict(event='blocked', reason='quota_or_deadline', at=time.time()))
                raise GatewayError('Phase quota or deadline exhausted.')
            slot = self.reserved + 1
            try:
                payload_sha = digest(canonical(payload))
                if self.phase == 'compat':
                    expected = self.plan['compatibility_payloads'][slot - 1]
                    if payload_sha != expected['sha256']:
                        raise ValueError
                    kind = expected['name']
                else:
                    kind = KINDS[slot - 1]
                    if (set(payload) != {'model', 'messages', 'temperature', 'max_tokens', 'response_format'}
                            or payload['model'] != self.plan['model'] or payload['temperature'] != 0
                            or type(payload['max_tokens']) is not int
                            or not 1 <= payload['max_tokens'] <= (400, 1800, 2600)[slot - 1]
                            or not isinstance(payload['messages'], list) or not 1 <= len(payload['messages']) <= 100
                            or any(not isinstance(m, dict) or set(m) != {'role', 'content'}
                                   or m['role'] not in {'system', 'user', 'assistant'} or not isinstance(m['content'], str)
                                   for m in payload['messages'])
                            or payload['response_format']['type'] != 'json_schema'
                            or payload['response_format']['json_schema']['name'] != kind
                            or payload['response_format']['json_schema']['strict'] is not True
                            or not isinstance(payload['response_format']['json_schema']['schema'], dict)):
                        raise ValueError
            except (ValueError, TypeError, KeyError):
                self.blocked = True
                self.append(dict(event='blocked', reason='payload_or_phase_mismatch', at=time.time()))
                raise GatewayError('Payload does not match the frozen phase slot.') from None
            row = dict(event='reserved', slot=slot, kind=kind, payload_sha256=payload_sha, at=time.time())
            # A failed durability operation poisons this in-memory phase too.
            self.blocked = True
            self.append(row)
            self.reserved += 1
            self.inflight = True
            self.blocked = False
            return row

    def finish(self, slot, outcome):
        with self.lock:
            complete = outcome['error'] is None and outcome['status'] == 200
            self.blocked = True
            self.append(dict(event='completed' if complete else 'failed', slot=slot, at=time.time(),
                             http_status=outcome['status'], body_complete=outcome['body_complete'],
                             response_sha256=digest(outcome['body']) if outcome['body_complete'] else None,
                             response_bytes=len(outcome['body']), error=outcome['error'],
                             elapsed_seconds=outcome['elapsed_seconds']))
            self.inflight = False
            if complete:
                self.completed += 1
                self.blocked = False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.stopping = True
            self.closed = True
            try:
                exclusive(self.directory / f'{self.phase}.closed.json',
                          dict(**self.health(), inflight=self.inflight, closed_at=time.time(),
                               ledger_sha256=digest(read_file(self.ledger_path))))
            finally:
                self._unlock()


def forward(url, payload, key, seconds):
    """One bounded request; no redirects, proxy inheritance, retry, or detached work."""
    started = time.monotonic()
    outcome = dict(status=None, body=b'', body_complete=False, error=None)

    async def perform():
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=seconds) as client:
            try:
                async with asyncio.timeout(seconds):
                    async with client.stream('POST', url, content=canonical(payload),
                                             headers={'Authorization': 'Bearer ' + key,
                                                      'Content-Type': 'application/json',
                                                      'Accept-Encoding': 'identity'}) as response:
                        outcome['status'] = response.status_code
                        if response.status_code != 200:
                            outcome['error'] = 'upstream_http_status'
                            return
                        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                            outcome['error'] = 'encoded_response_not_allowed'
                            return
                        length = response.headers.get('content-length')
                        if length is not None and (not length.isdecimal() or int(length) > MAX_BODY):
                            outcome['error'] = 'response_size_limit'
                            return
                        chunks = []
                        size = 0
                        async for chunk in response.aiter_raw():
                            size += len(chunk)
                            if size > MAX_BODY:
                                outcome['error'] = 'response_size_limit'
                                return
                            chunks.append(chunk)
                        outcome['body'] = b''.join(chunks)
                        outcome['body_complete'] = True
            except (TimeoutError, httpx.TimeoutException):
                outcome['error'] = 'upstream_deadline'
            except httpx.HTTPError:
                outcome['error'] = 'upstream_transport'
    try:
        with asyncio.Runner() as runner:
            runner.run(perform())
    except Exception:
        outcome['error'] = 'upstream_local_error'
    outcome['elapsed_seconds'] = round(time.monotonic() - started, 6)
    return outcome


def contains_secret(body, keys):
    """Reject response leakage, including nested JSON-escaped content, rather than rewrite it."""
    text = body.decode('utf-8', errors='replace')
    for _ in range(5):
        if any(key in text for key in keys):
            return True
        # Decoding each JSON string handles nested escaped assistant content.
        decoded = []
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
            try:
                value = json.loads(match.group())
                if isinstance(value, str):
                    decoded.append(value)
            except ValueError:
                pass
        next_text = '\n'.join(decoded)
        if not next_text or next_text == text:
            break
        text = next_text
    return False


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, session, caller_key, upstream_key, *, port=None):
        self.session = session
        self.caller_key = credential(caller_key)
        self.upstream_key = credential(upstream_key)
        if hmac.compare_digest(caller_key, upstream_key):
            raise GatewayError('Gateway and upstream credentials must differ.')
        self.connection_slots = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1', session.plan['listen_port'] if port is None else port), Handler)

    def process_request(self, request, client_address):
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()

    def handle_error(self, request, client_address):
        # No exception repr, URL, raw request, or header may reach stderr.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def setup(self):
        self.request.settimeout(5)
        super().setup()
        self.read_timer = threading.Timer(5, self.expire_read)
        self.read_timer.daemon = True
        self.read_timer.start()

    def expire_read(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self.read_timer.cancel()
        super().finish()

    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'gateway_http_request_rejected'})

    def reply(self, status, body):
        self.read_timer.cancel()
        raw = body if isinstance(body, bytes) else canonical(body)
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass
        self.close_connection = True

    def authorized(self):
        values = self.headers.get_all('Authorization', [])
        return len(values) == 1 and hmac.compare_digest(
            values[0].encode('utf-8'), ('Bearer ' + self.server.caller_key).encode('ascii'))

    def do_GET(self):
        if not self.authorized():
            self.reply(401, {'error': 'gateway_authentication_required'})
        elif self.path != '/health':
            self.reply(404, {'error': 'gateway_path_not_allowed'})
        else:
            self.reply(200, self.server.session.health())

    def do_POST(self):
        if not self.authorized():
            self.reply(401, {'error': 'gateway_authentication_required'})
            return
        if self.path != '/v1/chat/completions':
            self.reply(404, {'error': 'gateway_path_not_allowed'})
            return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or not lengths[0].isdecimal() or not 0 < int(lengths[0]) <= MAX_BODY
                    or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
                raise GatewayError('Invalid bounded request framing.')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise GatewayError('Incomplete request body.')
            payload = strict_json(raw)
            if not isinstance(payload, dict):
                raise GatewayError('Request body must be an object.')
            if contains_secret(raw, (self.server.caller_key, self.server.upstream_key)):
                raise GatewayError('Request body contains credential material.')
        except (GatewayError, OSError):
            self.reply(400, {'error': 'gateway_invalid_request'})
            return
        self.read_timer.cancel()
        session = self.server.session
        try:
            row = session.reserve(payload)
        except GatewayError:
            self.reply(409, {'error': 'gateway_phase_or_quota_rejected'})
            return
        except Exception:
            self.reply(503, {'error': 'gateway_reservation_not_durable'})
            return
        seconds = min(session.plan['request_seconds'], session.deadline - time.monotonic())
        if seconds <= 0:
            outcome = dict(status=None, body=b'', body_complete=False, error='phase_deadline', elapsed_seconds=0)
        else:
            outcome = forward(session.plan['upstream'] + '/chat/completions', payload, self.server.upstream_key, seconds)
        if contains_secret(outcome['body'], (self.server.caller_key, self.server.upstream_key)):
            outcome['body'] = b''
            outcome['body_complete'] = False
            outcome['error'] = 'upstream_credential_leak_blocked'
        try:
            session.finish(row['slot'], outcome)
        except Exception:
            self.reply(503, {'error': 'gateway_completion_not_durable'})
            return
        if outcome['error']:
            self.reply(504 if 'deadline' in outcome['error'] else 502, {'error': outcome['error']})
        else:
            self.reply(200, outcome['body'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--compatibility-plan', type=Path, required=True)
    p.add_argument('--upstream', required=True)
    p.add_argument('--listen-port', type=int, default=18081)
    p.add_argument('--upstream-mode', choices=('ssh_tunnel', 'loopback_fixture'), default='ssh_tunnel')
    p.add_argument('--request-seconds', type=float, default=40)
    p.add_argument('--phase-seconds', type=float, default=300)
    p = sub.add_parser('serve')
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--phase', choices=PHASES, required=True)
    p.add_argument('--compatibility-summary', type=Path)
    args = parser.parse_args(argv)
    session = server = None
    exit_code = 0
    try:
        if args.command == 'prepare':
            result = prepare(args.directory, compatibility_plan=args.compatibility_plan,
                             upstream=args.upstream, listen_port=args.listen_port, upstream_mode=args.upstream_mode,
                             request_seconds=args.request_seconds, phase_seconds=args.phase_seconds)
            print(json.dumps(result))
            return 0
        caller, upstream = credential(os.getenv(CALLER_KEY_ENV)), credential(os.getenv(UPSTREAM_KEY_ENV))
        if caller == upstream:
            raise GatewayError('Credentials must differ.')
        session = Session(args.directory, args.phase, compatibility_summary=args.compatibility_summary)
        server = GatewayServer(session, caller, upstream)
        def stop(*_):
            session.stop_accepting()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        timer = threading.Timer(session.plan['phase_seconds'], stop)
        timer.daemon = True
        timer.start()
        try:
            print(json.dumps(session.health()), flush=True)
            server.serve_forever(poll_interval=.1)
        finally:
            timer.cancel()
            server.server_close()  # Join bounded in-flight handlers before closing the ledger.
    except Exception:
        print(json.dumps({'status': 'blocked', 'error': 'Gateway initialization or evidence operation failed.'}))
        exit_code = 2
    finally:
        try:
            if server is not None:
                server.server_close()
            if session is not None:
                session.close()
        except Exception:
            print(json.dumps({'status': 'blocked', 'error': 'Gateway shutdown evidence could not be saved.'}))
            exit_code = 2
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
