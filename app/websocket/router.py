"""Project-scoped WebSocket endpoint.

Authentication:
- Prefer Authorization: Bearer <token> header.
- Fall back to ?token=<raw> for browser clients that cannot set headers.
- Never log raw tokens.

Authorization:
- The device token must be valid and not revoked.
- The project must exist.

Catch-up:
- On connect, the client receives a `hello` message.
- Any events emitted after subscription are streamed as JSON.
- Missed events (during disconnect or overflow) are fetched via
  GET /projects/{id}/events?since=<sequence>.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.deps import LAST_SEEN_UPDATE_INTERVAL_SECONDS
from app.auth.security import hash_device_token
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.websocket.manager import get_manager
from fastapi import Depends
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(tags=["websocket"])

PING_INTERVAL_SECONDS = 25.0


def _extract_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization")
    if header:
        parts = header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    token = websocket.query_params.get("token")
    if token:
        return token.strip()
    return None


def _authenticate(db: Session, raw_token: str | None) -> Device | None:
    if not raw_token:
        return None
    token_hash = hash_device_token(raw_token)
    device = db.execute(
        select(Device).where(
            Device.token_hash == token_hash,
            Device.revoked.is_(False),
        )
    ).scalar_one_or_none()

    if device is not None:
        # Throttled update of last_seen_at
        now = datetime.now(timezone.utc)
        if (
            device.last_seen_at is None
            or (now - device.last_seen_at).total_seconds()
            > LAST_SEEN_UPDATE_INTERVAL_SECONDS
        ):
            device.last_seen_at = now
            db.add(device)
            db.commit()

    return device


def _project_exists(db: Session, project_id: uuid.UUID) -> bool:
    return db.get(Project, project_id) is not None


@router.websocket("/projects/{project_id}/ws")
async def project_ws(
    websocket: WebSocket,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    await websocket.accept()

    raw_token = _extract_token(websocket)
    device = _authenticate(db, raw_token)
    if device is None:
        await websocket.send_json(
            {"type": "error", "detail": "Invalid or revoked device token"}
        )
        await websocket.close(code=1008)
        return

    if not _project_exists(db, project_id):
        await websocket.send_json(
            {"type": "error", "detail": "Project not found"}
        )
        await websocket.close(code=1008)
        return

    manager = get_manager()
    queue = await manager.subscribe(project_id)

    log.info(
        "ws_connected",
        project_id=str(project_id),
        device_id=str(device.id),
    )

    try:
        await websocket.send_json(
            {
                "type": "hello",
                "project_id": str(project_id),
                "device_id": str(device.id),
            }
        )

        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=PING_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue

            await websocket.send_json(event)

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception(
            "ws_error",
            project_id=str(project_id),
            device_id=str(device.id),
        )
    finally:
        await manager.unsubscribe(project_id, queue)
        log.info(
            "ws_disconnected",
            project_id=str(project_id),
            device_id=str(device.id),
        )
