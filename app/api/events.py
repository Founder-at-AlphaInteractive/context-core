"""Event log + catch-up API routes."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.event import Event
from app.models.project import Project
from app.schemas.event import EventPage, EventRead

router = APIRouter(prefix="/projects/{project_id}/events", tags=["events"])


@router.get("", response_model=EventPage)
def list_events(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> EventPage:
    """Deterministic, project-scoped event log catch-up.

    Events are returned in strictly ascending sequence order (> since).
    The authoritative synchronization horizon is project.last_event_sequence.
    """
    events = list(
        db.execute(
            select(Event)
            .where(
                Event.project_id == project.id,
                Event.sequence > since,
            )
            .order_by(Event.sequence.asc())
            .limit(limit)
        ).scalars()
    )

    return EventPage(
        events=[EventRead.model_validate(e) for e in events],
        latest_sequence=project.last_event_sequence,
    )
