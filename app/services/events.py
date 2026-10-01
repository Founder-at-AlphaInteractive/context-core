"""Event log writer.

Every meaningful mutation writes an event with a monotonic per-project
sequence number. Sequence allocation happens under a row lock on the
project, inside the caller's transaction.

Events are queued in Session.info["_pending_events_stack"] and published to the
WebSocket bridge after the transaction commits (see app/db/session.py).
This avoids broadcasting events that later roll back.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EventType
from app.models.event import Event
from app.models.project import Project

PENDING_STACK_KEY = "_pending_events_stack"


def serialize_event(event: Event) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "project_id": str(event.project_id),
        "sequence": event.sequence,
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "entity_id": str(event.entity_id) if event.entity_id else None,
        "payload": event.payload or {},
        "client_id": str(event.client_id) if event.client_id else None,
        "created_at": (
            event.created_at.isoformat() if event.created_at is not None else None
        ),
    }


class EventEmitter:
    def __init__(self, db: Session):
        self.db = db

    def emit(
        self,
        *,
        project_id: uuid.UUID,
        event_type: EventType,
        entity_id: uuid.UUID | None = None,
        payload: dict | None = None,
        client_id: uuid.UUID | None = None,
    ) -> Event:
        project = self.db.execute(
            select(Project).where(Project.id == project_id).with_for_update()
        ).scalar_one_or_none()

        if project is None:
            raise ValueError(
                f"Project {project_id} not found while emitting event"
            )

        project.last_event_sequence = project.last_event_sequence + 1
        seq = project.last_event_sequence

        event = Event(
            project_id=project_id,
            sequence=seq,
            type=event_type,
            entity_id=entity_id,
            payload=payload or {},
            client_id=client_id,
        )
        self.db.add(event)
        self.db.flush()

        # Queue serialized event on the top frame of the pending events stack
        stack = self.db.info.setdefault(PENDING_STACK_KEY, [[]])
        stack[-1].append(serialize_event(event))
        return event


def emit_event(
    db: Session,
    *,
    project_id: uuid.UUID,
    event_type: EventType,
    entity_id: uuid.UUID | None = None,
    payload: dict | None = None,
    client_id: uuid.UUID | None = None,
) -> Event:
    return EventEmitter(db).emit(
        project_id=project_id,
        event_type=event_type,
        entity_id=entity_id,
        payload=payload,
        client_id=client_id,
    )
