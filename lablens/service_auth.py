"""Service identity for the private Python worker, separate from browser sessions.

The worker does not interpret workspace membership: the JVM authorizes the user
and binds a snapshot before forwarding it. This credential authenticates that
service hop; it must never be embedded in the browser, model input, or telemetry.
"""
from contextlib import asynccontextmanager
import hmac
import ipaddress
import os

from starlette.responses import JSONResponse

HEADER = 'X-LabLens-Service-Key'
_HEADER_BYTES = HEADER.lower().encode('ascii')


class ServiceAuthConfigError(RuntimeError):
    pass


def _configuration():
    value = os.getenv('LABLENS_AUTH_ENABLED', 'true').lower()
    if value not in {'true', 'false'}:
        raise ServiceAuthConfigError('LABLENS_AUTH_ENABLED must be true or false.')
    if value == 'false':
        return False, b''
    key = os.getenv('LABLENS_SERVICE_KEY', '')
    # A generated token is expected. Restrict its wire representation so merged
    # or comma-separated credentials cannot be interpreted as one valid key.
    if not 32 <= len(key) <= 512 or any(not 33 <= ord(char) <= 126 or char == ',' for char in key):
        raise ServiceAuthConfigError('Set LABLENS_SERVICE_KEY to a generated 32-512 character ASCII token without whitespace or commas.')
    return True, key.encode('ascii')


@asynccontextmanager
async def service_auth_lifespan(_app):
    # Fail startup, rather than launching an accidentally public model worker.
    _configuration()
    yield


def service_headers():
    """Headers for a previously validated, loopback-only Agent tool endpoint."""
    enabled, key = _configuration()
    return {HEADER: key.decode('ascii')} if enabled else {}


def _loopback(client):
    try:
        return client is not None and ipaddress.ip_address(client[0]).is_loopback
    except (ValueError, TypeError, IndexError):
        return False


class ServiceAuthMiddleware:
    """Guard every API route before request parsing, cancellation, or provider work."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path'] == '/api' or scope['path'].startswith('/api/')):
            await self.app(scope, receive, send)
            return
        # Recheck here as well: tests and alternate ASGI hosts may skip lifespan.
        # These error bodies deliberately contain neither supplied nor expected keys.
        try:
            enabled, expected = _configuration()
        except ServiceAuthConfigError:
            response = JSONResponse({'detail': 'Worker service authentication is not configured.'}, status_code=503)
            await response(scope, receive, send)
            return
        if not enabled:
            allowed = _loopback(scope.get('client'))
        else:
            values = [value for key, value in scope.get('headers', []) if key.lower() == _HEADER_BYTES]
            allowed = len(values) == 1 and hmac.compare_digest(values[0], expected)
        if not allowed:
            response = JSONResponse({'detail': 'Worker service authentication required.'}, status_code=401,
                                    headers={'Cache-Control': 'no-store'})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
