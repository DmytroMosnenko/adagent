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
import json
from typing import Awaitable, Callable, TypeVar

from .logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class BrokerGateClient:
    def __init__(self, resource: str, socket_path: str):
        self.resource = resource
        self.socket_path = socket_path

    async def run(self, owner_id: str, coro_fn: Callable[[], Awaitable[T]]) -> T:
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        except OSError as exc:
            raise ConnectionError(
                f"[broker:{self.resource}] can't reach concurrency_broker.py at "
                f"{self.socket_path} — is it running? ({exc})"
            ) from exc

        try:
            writer.write((json.dumps({"resource": self.resource, "owner": str(owner_id)}) + "\n").encode())
            await writer.drain()

            line = await reader.readline()
            if not line:
                raise ConnectionError(f"[broker:{self.resource}] connection closed before grant")
            resp = json.loads(line)
            if resp.get("status") != "granted":
                raise RuntimeError(f"[broker:{self.resource}] denied: {resp}")

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
