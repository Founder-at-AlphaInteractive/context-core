"""Project-scoped WebSocket connection manager.

Each subscriber gets its own bounded asyncio.Queue. The manager keeps
only live connections; it is NOT a persistence layer. Missed events are
recovered via the REST endpoint GET /projects/{id}/events?since=N.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

MAX_QUEUE_SIZE = 1000


class ConnectionManager:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_id: uuid.UUID) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.setdefault(project_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self, project_id: uuid.UUID, queue: asyncio.Queue
    ) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(project_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(project_id, None)

    async def broadcast(
        self, project_id: uuid.UUID, event_payload: dict[str, Any]
    ) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(project_id, ()))

        for queue in subscribers:
            try:
                queue.put_nowait(event_payload)
            except asyncio.QueueFull:
                # Slow client: notify client that queue overflowed and resync is required.
                try:
                    # Drop oldest item to make room for sync_required notice
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    queue.put_nowait({
                        "type": "sync_required",
                        "project_id": str(project_id),
                        "reason": "queue_overflow",
                    })
                except Exception:
                    # Subscriber is completely unresponsive
                    pass

    def subscriber_count(self, project_id: uuid.UUID) -> int:
        return len(self._subscribers.get(project_id, ()))


_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
