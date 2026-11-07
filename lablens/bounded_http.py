"""One synchronous call backed by cancellable async HTTP, without detached work.

HTTPX phase timeouts bound inactivity, not the wall clock of a trickling body.
An asyncio deadline bounds this whole call; registry cancellation interrupts its
task and unwinds the HTTP stream/client before this function returns. This is a
sync worker API, not an API for callers already running an asyncio event loop.
No requests are retried. Closing the socket cannot cancel provider-side billing.
OS resolver/TLS work may be non-preemptible. Required cleanup is awaited, so the
deadline is not a universal real-time guarantee under OS stalls.
"""
import asyncio
import math
from threading import Lock
import time

import httpx

from .cancellation import ExecutionCancelled, check_cancelled, on_cancellation


class HTTPDeadlineExceeded(httpx.TimeoutException):
    """The absolute call deadline expired, rather than a read inactivity limit."""


def post(url, *, json, headers, timeout, follow_redirects=False, read_timeout=None, trust_env=True):
    """Return a complete HTTPX response or unwind the entire in-flight operation.

    ``timeout`` is an absolute seconds budget measured before loop construction.
    ``read_timeout`` optionally imposes a shorter read inactivity limit. All
    other phase limits retain ``timeout``. A deadline/cancelled partial response
    has unknown usage even when the server has already sent response headers.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('HTTP absolute timeout must be finite and positive')
    if read_timeout is not None and (not math.isfinite(read_timeout) or read_timeout <= 0):
        raise ValueError('HTTP read timeout must be finite and positive')
    check_cancelled()
    deadline = time.monotonic() + timeout
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('bounded_http.post must run in a synchronous worker thread')

    async def request():
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        cancellation_lock, cancellation_sent = Lock(), False
        active = True

        def cancel_if_active():
            # This executes on the loop thread, after any synchronous scope
            # exit. A callback snapshotted before unsubscribe can arrive late.
            if active and not task.done():
                task.cancel()

        def cancel():
            nonlocal cancellation_sent
            # A callback snapshotted just before unsubscribe may arrive after
            # Runner closes. It must not break durable cancellation acknowledgement.
            with cancellation_lock:
                if cancellation_sent:
                    return
                cancellation_sent = True
            try:
                loop.call_soon_threadsafe(cancel_if_active)
            except RuntimeError:
                pass

        # The timeout and callback scope end before final client cleanup. A
        # deadline racing with cancellation must not interrupt aclose halfway
        # through and leave its connections unowned. Cleanup remains synchronous
        # to our caller, even if an OS operation makes it exceed the deadline.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, read=read_timeout or timeout),
            follow_redirects=follow_redirects,
            trust_env=trust_env,
        ) as client:
            try:
                with on_cancellation(cancel):
                    check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HTTPDeadlineExceeded('Absolute HTTP request deadline exceeded')
                    try:
                        async with asyncio.timeout(remaining):
                            response = await client.post(url, json=json, headers=headers)
                            check_cancelled()
                            return response
                    except asyncio.CancelledError:
                        check_cancelled()
                        raise
                    except TimeoutError:
                        # If both signals become visible together, cancellation wins;
                        # neither outcome is allowed to return a partial response.
                        check_cancelled()
                        raise HTTPDeadlineExceeded('Absolute HTTP request deadline exceeded') from None
            finally:
                active = False

    with asyncio.Runner() as runner:
        return runner.run(request())
