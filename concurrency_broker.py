"""
Standalone concurrency-arbitration broker for AdAgent.

Runs as its own process, independent of adagent_server.py's uvicorn workers,
so SCRAPER_MAX_CONCURRENT_TOTAL / OPENAI_MAX_CONCURRENT_TOTAL are true
process-wide totals shared by every worker — not split per worker. Whichever
worker actually has traffic gets access to the full limit.

See service/fair_queue.py for the round-robin fairness algorithm (kept
generic and unit-testable in isolation) and service/broker_client.py for the
client side used by scraper.py / ai_client.py.

Protocol — newline-delimited JSON over a Unix domain socket, one long-lived
connection per gated operation:

    client -> broker   {"resource": "scraper"|"openai", "owner": "<report_id>"}
    broker -> client    ... blocks until a slot is available for that resource ...
                        {"status": "granted"}
    ... client does its real work locally (scrape / OpenAI call) ...
    client -> broker   {"action": "release"}
    (connection closes)

Crash safety: if the client disconnects at any point — while queued waiting
for a grant, or after being granted but before sending "release" (e.g. the
worker process was killed mid-request) — the broker frees the slot
immediately. No TTL/heartbeat scheme is needed: a dead connection is
detected the moment the broker's next read on it returns EOF.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# add repo root (../) to sys.path, same convention as adagent_server.py
sys.path.append(str(Path(__file__).resolve().parents[1]))

from service.config import settings
from service.fair_queue import FairGate
from service.logger import get_logger, setup_root_logging

setup_root_logging()
logger = get_logger(__name__)

GATES: dict[str, FairGate] = {
    "scraper": FairGate(settings.SCRAPER_MAX_CONCURRENT_TOTAL, name="scraper"),
    "openai":  FairGate(settings.OPENAI_MAX_CONCURRENT_TOTAL, name="openai"),
}


async def _read_json_line(reader: asyncio.StreamReader) -> dict | None:
    line = await reader.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername") or writer.get_extra_info("sockname")
    granted_gate: FairGate | None = None
    resource = owner = None

    logger.debug("[broker] connection opened: %s", peer)

    try:
        req = await _read_json_line(reader)
        if not req:
            logger.debug("[broker] %s disconnected before sending a request", peer)
            return

        resource = req.get("resource")
        owner = req.get("owner")
        gate = GATES.get(resource)
        if gate is None or not owner:
            logger.debug("[broker] %s sent invalid request: %s", peer, req)
            writer.write((json.dumps({"status": "error", "message": "invalid resource/owner"}) + "\n").encode())
            await writer.drain()
            return

        logger.debug(
            "[broker] %s requesting resource=%s owner=%s | before: %s",
            peer, resource, owner, gate.stats(),
        )

        # Race the acquire against the client disconnecting before it's
        # granted (e.g. it gave up, or the worker process died while queued).
        acquire_task = asyncio.create_task(gate.acquire(str(owner)))
        disconnect_task = asyncio.create_task(reader.read(1))

        done, _ = await asyncio.wait(
            {acquire_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if acquire_task not in done:
            # Disconnected before being granted — cancel the pending acquire
            # so fair_queue.FairGate skips it for free (see the `fut.done()`
            # check in its dispatch loop) instead of burning a slot on it.
            acquire_task.cancel()
            try:
                await acquire_task
            except asyncio.CancelledError:
                logger.debug(
                    "[broker] %s disconnected while WAITING for resource=%s owner=%s (no slot consumed)",
                    peer, resource, owner,
                )
            else:
                # Lost the cancellation race — the grant landed anyway right
                # as we tried to cancel it. Client's already gone, so free
                # the slot immediately instead of leaking it.
                gate.release()
                logger.debug(
                    "[broker] %s disconnected right at grant time for resource=%s owner=%s — "
                    "released immediately | after: %s",
                    peer, resource, owner, gate.stats(),
                )
            return

        disconnect_task.cancel()
        try:
            await disconnect_task
        except asyncio.CancelledError:
            pass

        # Granted.
        granted_gate = gate
        logger.debug(
            "[broker] GRANTED resource=%s owner=%s to %s | after: %s",
            resource, owner, peer, gate.stats(),
        )
        writer.write((json.dumps({"status": "granted"}) + "\n").encode())
        await writer.drain()

        # Block until the client explicitly releases, or disconnects
        # (normal completion, or a crash) — either way we fall through
        # to the `finally` below and free the slot.
        release_msg = None
        try:
            release_msg = await reader.readline()
        except Exception:
            pass

        if release_msg:
            logger.debug(
                "[broker] %s released resource=%s owner=%s explicitly",
                peer, resource, owner,
            )
        else:
            logger.debug(
                "[broker] %s disconnected WITHOUT releasing resource=%s owner=%s "
                "(worker crash / connection drop?) — freeing slot anyway",
                peer, resource, owner,
            )

    except Exception as exc:
        logger.warning("[broker] connection %s error: %s", peer, exc)
    finally:
        if granted_gate is not None:
            granted_gate.release()
            logger.debug(
                "[broker] slot freed for resource=%s owner=%s | after: %s",
                resource, owner, granted_gate.stats(),
            )
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.debug("[broker] connection closed: %s", peer)


async def main() -> None:
    socket_path = settings.CONCURRENCY_BROKER_SOCKET
    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(socket_path):
        os.unlink(socket_path)  # stale socket from a previous crashed run

    server = await asyncio.start_unix_server(handle_connection, path=socket_path)
    os.chmod(socket_path, 0o660)

    logger.info(
        "[broker] listening on %s — scraper=%d, openai=%d",
        socket_path, settings.SCRAPER_MAX_CONCURRENT_TOTAL, settings.OPENAI_MAX_CONCURRENT_TOTAL,
    )

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
