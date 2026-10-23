"""In-process fan-out of live run events to SSE subscribers. Events are JSON-ready dicts (the agent event protocol)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

CLOSED: dict[str, Any] = {"kind": "__closed__"}  # sentinel: no more events for this run
LAGGED: dict[str, Any] = {
    "kind": "__lagged__"
}  # sentinel: subscriber was too slow and was dropped; it may reconnect


class Subscription:
    def __init__(self, maxsize: int = 1000) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize)

    async def get(self, wait_s: float) -> dict[str, Any] | None:
        """Next event, or None on timeout (the caller sends a keep-alive)."""
        try:
            return await asyncio.wait_for(self.queue.get(), wait_s)
        except TimeoutError:
            return None


class RunBus:
    def __init__(self) -> None:
        self._subs: dict[UUID, set[Subscription]] = {}

    def subscribe(self, run_id: UUID) -> Subscription:
        sub = Subscription()
        self._subs.setdefault(run_id, set()).add(sub)
        return sub

    def unsubscribe(self, run_id: UUID, sub: Subscription) -> None:
        subs = self._subs.get(run_id)
        if subs:
            subs.discard(sub)
            if not subs:
                del self._subs[run_id]

    def publish(self, run_id: UUID, event: dict[str, Any]) -> None:
        for sub in list(self._subs.get(run_id, ())):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Never block the run on a slow browser tab: drop it. It reconnects and replays from the database.
                self.unsubscribe(run_id, sub)
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                sub.queue.put_nowait(LAGGED)

    def close(self, run_id: UUID) -> None:
        self.publish(run_id, CLOSED)

    def subscriber_count(self, run_id: UUID) -> int:
        return len(self._subs.get(run_id, ()))
