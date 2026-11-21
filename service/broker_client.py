"""
Client for concurrency_broker.py.

Connects over the shared Unix domain socket and exposes the same
`run(owner_id, coro_fn)` interface as a local service.fair_queue.FairGate,
so scraper.py / ai_client.py don't need to know or care whether arbitration
happens in-process or in the standalone broker process.

One connection per gated operation (per ad scrape, per OpenAI call) — see
concurrency_broker.py's module docstring for the wire protocol and the
crash-safety reasoning.
"""
from __future__ import annotations

import asyncio
import errno
import json
import random
import time
from typing import Awaitable, Callable, TypeVar

from .logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# Connect/handshake failures happen *before* a slot is granted, so retrying them is
# always safe: the broker frees nothing it never handed out, and coro_fn() has not
# started. (Once granted, coro_fn() is never retried here.)
_HANDSHAKE_ATTEMPTS = 6
_HANDSHAKE_BACKOFF = 0.1     # seconds, doubled per attempt (capped at 2s) + jitter
# Out of file descriptors (EMFILE/ENFILE) is transient: other in-flight work frees fds as it
# finishes. Don't burn the 6 quick attempts (~3s) on it — keep waiting up to this long.
_FD_EXHAUSTED_PATIENCE = 60.0
_FD_ERRNOS = (errno.EMFILE, errno.ENFILE)


class BrokerGateClient:
    def __init__(self, resource: str, socket_path: str):
        self.resource = resource
        self.socket_path = socket_path

    async def _acquire(self, owner_id: str):
        """
        Connect and wait for the grant. Returns (reader, writer) holding a slot.

        A connection that is dropped, reset or refused before the grant (e.g. a
        burst bigger than the broker's accept queue) is retried with backoff
        instead of failing the caller's whole report.
        """
        last_exc: Exception | None = None
        fd_deadline = time.monotonic() + _FD_EXHAUSTED_PATIENCE
        attempt = -1
        while True:
            attempt += 1
            if attempt >= _HANDSHAKE_ATTEMPTS:
                break
            writer = None
            try:
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
                writer.write((json.dumps({"resource": self.resource, "owner": str(owner_id)}) + "\n").encode())
                await writer.drain()

                line = await reader.readline()      # blocks until a slot is free (can be minutes)
                if not line:
                    raise ConnectionError("connection closed before grant")
                resp = json.loads(line)
                if resp.get("status") != "granted":
                    raise RuntimeError(f"[broker:{self.resource}] denied: {resp}")
                return reader, writer
            except OSError as exc:                  # ConnectionError & friends are OSErrors
                last_exc = exc
                if writer is not None:
                    writer.close()
                if exc.errno in _FD_ERRNOS and time.monotonic() < fd_deadline:
                    attempt -= 1                              # does not consume a normal attempt
                    delay = random.uniform(0.5, 2.0)
                    logger.warning("[broker:%s] out of file descriptors (%s) — retry in %.2fs",
                                   self.resource, exc, delay)
                    await asyncio.sleep(delay)
                elif attempt + 1 < _HANDSHAKE_ATTEMPTS:
                    delay = min(_HANDSHAKE_BACKOFF * 2 ** attempt, 2.0) + random.uniform(0, 0.1)
                    logger.warning("[broker:%s] handshake failed (%s) — retry %d/%d in %.2fs",
                                   self.resource, exc, attempt + 1, _HANDSHAKE_ATTEMPTS - 1, delay)
                    await asyncio.sleep(delay)
            except BaseException:
                # cancelled / protocol error: don't leak the socket (the broker
                # treats the EOF as "client gave up" and frees the slot / queue entry)
                if writer is not None:
                    writer.close()
                raise
        raise ConnectionError(
            f"[broker:{self.resource}] can't get a slot from concurrency_broker.py at "
            f"{self.socket_path} after {_HANDSHAKE_ATTEMPTS} attempts — is it running? ({last_exc})"
        ) from last_exc

    async def run(self, owner_id: str, coro_fn: Callable[[], Awaitable[T]]) -> T:
        reader, writer = await self._acquire(owner_id)
        try:
            try:
                return await coro_fn()
            finally:
                # Best-effort explicit release — if this write fails, closing
                # the connection below (EOF on the broker's side) still frees
                # the slot, so a flaky write here never masks coro_fn()'s
                # real result/exception.
                try:
                    writer.write((json.dumps({"action": "release"}) + "\n").encode())
                    await writer.drain()
                except Exception as exc:
                    logger.warning("[broker:%s] release message failed: %s", self.resource, exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
