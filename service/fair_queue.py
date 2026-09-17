"""
Global fair-scheduling gate.

Bounds how many coroutines run concurrently against an external limit (a
ScraperAPI concurrent-thread plan, an OpenAI concurrent-request budget) while
making sure concurrent *owners* (reports) get an even, round-robin share of
the available slots instead of plain FIFO ordering.

Why not a plain asyncio.Semaphore? A semaphore grants slots strictly in the
order acquire() was called. If a 100-ad report enqueues its 100 acquisitions
a moment before a 9-ad report enqueues its 9, the small report is stuck
behind almost all of the large one — exactly the "second user waits for the
first user's 100 ads" problem this is meant to avoid.

FairGate instead keeps one FIFO queue per owner_id. Whenever a slot frees up,
it is handed to the next owner in rotation (round-robin), not to whichever
owner happened to enqueue first. An owner with no pending work is skipped
and removed from rotation until it submits something new.

One process-wide instance is created per resource (see scraper.py's
`scrape_gate` and ai_client.py's `ai_gate`). Because this lives in a single
asyncio event loop, it only coordinates within one process — with multiple
uvicorn workers, divide the total external limit by the worker count when
sizing `capacity` (see Settings.scraper_max_concurrent_per_worker /
openai_max_concurrent_per_worker).
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class FairGate:
    def __init__(self, capacity: int, name: str = "gate"):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.name = name

        self._owners: dict[str, deque] = {}     # owner_id -> deque[(future, coro_fn)]
        self._rotation: deque[str] = deque()     # round-robin order of owners with pending work
        self._running = 0
        self._wakeup = asyncio.Event()
        self._worker_task: asyncio.Task | None = None

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._dispatch_loop())

    async def acquire(self, owner_id: str) -> None:
        """
        Block until a slot is granted to `owner_id`. Must be paired with a
        matching release() — prefer run() or a try/finally.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        queue = self._owners.setdefault(owner_id, deque())
        queue.append(fut)
        if owner_id not in self._rotation:
            self._rotation.append(owner_id)

        self._ensure_worker()
        self._wakeup.set()

        await fut

    def release(self) -> None:
        """Free the slot taken by a prior acquire() and wake the dispatcher."""
        self._running -= 1
        self._wakeup.set()

    async def run(self, owner_id: str, coro_fn: Callable[[], Awaitable[T]]) -> T:
        """
        Convenience wrapper: acquire a slot for `owner_id`, run `coro_fn`
        (a zero-arg callable so the underlying work — e.g. the actual HTTP
        call — only starts once the gate grants the slot, not at submission
        time), then release the slot whether or not it raised.
        """
        await self.acquire(owner_id)
        try:
            return await coro_fn()
        finally:
            self.release()

    async def _dispatch_loop(self) -> None:
        while True:
            if self._running >= self.capacity or not self._rotation:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue

            owner_id = self._rotation.popleft()
            queue = self._owners.get(owner_id)
            if not queue:
                continue  # shouldn't happen, but stay defensive

            fut = queue.popleft()
            if queue:
                self._rotation.append(owner_id)  # more work from this owner — cycle to the back
            else:
                del self._owners[owner_id]

            if fut.done():
                # Already cancelled/abandoned (e.g. the caller disconnected
                # before its turn came up) — skip it without consuming a
                # slot, or capacity would leak every time this happens.
                continue

            self._running += 1
            fut.set_result(None)

    def stats(self) -> dict:
        """For logging/debugging — not used on the hot path."""
        return {
            "name": self.name,
            "capacity": self.capacity,
            "running": self._running,
            "free": self.capacity - self._running,
            "owners_waiting": len(self._rotation),
            "queued_total": sum(len(q) for q in self._owners.values()),
        }
