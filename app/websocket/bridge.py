"""Bridge between sync event emission (services) and async WebSocket
broadcast (ConnectionManager).

The FastAPI lifespan installs the running loop and manager once at
startup. `publish_event_sync` is safe to call from any thread; if no
loop is installed (e.g. unit tests) it is a no-op.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.logging_config import redact
from app.websocket.manager import ConnectionManager

_loop: asyncio.AbstractEventLoop | None = None
_manager: ConnectionManager | None = None


def install(loop: asyncio.AbstractEventLoop, manager: ConnectionManager) -> None:
    global _loop, _manager
    _loop = loop
    _manager = manager


def uninstall() -> None:
    global _loop, _manager
    _loop = None
    _manager = None


def publish_event_sync(event_payload: dict[str, Any]) -> None:
    """Schedule an async broadcast on the installed event loop."""
    if _loop is None or _manager is None:
        return

    project_id_raw = event_payload.get("project_id")
    if project_id_raw is None:
        return
    try:
        project_id = uuid.UUID(str(project_id_raw))
    except (ValueError, TypeError):
        return

    payload = redact(event_payload)

    try:
        if _loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _manager.broadcast(project_id, payload),
                _loop,
            )
    except (RuntimeError, ValueError):
        # Loop closed or shutting down — safe to ignore.
        return
