"""Handoff API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.handoff import HandoffRead, HandoffRequest
from app.services import handoff as handoff_service

router = APIRouter(prefix="/projects/{project_id}/handoffs", tags=["handoffs"])


@router.post("", response_model=HandoffRead, status_code=status.HTTP_201_CREATED)
def create_handoff(
    payload: HandoffRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> HandoffRead:
    try:
        handoff = handoff_service.generate_handoff(
            db,
            project=project,
            to_role=payload.to_role,
            to_ai_profile_id=payload.to_ai_profile_id,
            from_role=payload.from_role,
            from_ai_profile_id=payload.from_ai_profile_id,
            topic=payload.topic,
            task=payload.task,
            token_budget=payload.token_budget,
            client_id=device.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return HandoffRead.model_validate(handoff)


@router.get("", response_model=list[HandoffRead])
def list_handoffs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> list[HandoffRead]:
    items = handoff_service.list_handoffs(db, project.id, limit=limit)
    return [HandoffRead.model_validate(h) for h in items]


@router.get("/{handoff_id}", response_model=HandoffRead)
def read_handoff(
    handoff_id: uuid.UUID,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> HandoffRead:
    handoff = handoff_service.get_handoff(db, project.id, handoff_id)
    if handoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Handoff not found"
        )
    return HandoffRead.model_validate(handoff)
