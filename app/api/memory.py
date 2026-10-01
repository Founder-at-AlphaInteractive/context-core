"""Memory API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import MemoryStatus, MemoryType
from app.models.project import Project
from app.schemas.memory import (
    MemoryCreateResult,
    MemoryItemCreate,
    MemoryItemRead,
    MemoryItemUpdate,
)
from app.services import memory as memory_service

router = APIRouter(prefix="/projects/{project_id}/memory", tags=["memory"])


@router.get("", response_model=list[MemoryItemRead])
def list_memory(
    type: MemoryType | None = Query(default=None),
    status_filter: MemoryStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> list[MemoryItemRead]:
    items = memory_service.list_memory(
        db, project.id, memory_type=type, status=status_filter, limit=limit
    )
    return [MemoryItemRead.model_validate(i) for i in items]


@router.post(
    "",
    response_model=MemoryCreateResult,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    payload: MemoryItemCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> MemoryCreateResult:
    try:
        item, conflicts = memory_service.create_memory(
            db, project_id=project.id, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    return MemoryCreateResult(
        memory=MemoryItemRead.model_validate(item),
        conflicts=conflicts,
    )


@router.get("/{memory_id}", response_model=MemoryItemRead)
def read_memory(
    memory_id: uuid.UUID,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> MemoryItemRead:
    item = memory_service.get_memory(db, project.id, memory_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory item not found"
        )
    return MemoryItemRead.model_validate(item)


@router.patch("/{memory_id}", response_model=MemoryCreateResult)
def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryItemUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> MemoryCreateResult:
    item = memory_service.get_memory(db, project.id, memory_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory item not found"
        )
    try:
        updated, conflicts = memory_service.update_memory(
            db, project_id=project.id, item=item, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    return MemoryCreateResult(
        memory=MemoryItemRead.model_validate(updated),
        conflicts=conflicts,
    )
