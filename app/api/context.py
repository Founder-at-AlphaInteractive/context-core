"""Context compiler API routes."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.context import ContextRequest, ContextSnapshotRead
from app.services import context_compiler

router = APIRouter(prefix="/projects/{project_id}/context", tags=["context"])


@router.post("", response_model=ContextSnapshotRead, status_code=status.HTTP_201_CREATED)
def generate_context(
    payload: ContextRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ContextSnapshotRead:
    try:
        compiled = context_compiler.compile_context(
            db,
            project=project,
            role=payload.role,
            task=payload.task,
            token_budget=payload.token_budget,
            include_recent_messages=20,
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if payload.persist_snapshot:
        snapshot = context_compiler.persist_snapshot(
            db,
            project_id=project.id,
            role=payload.role,
            task=payload.task,
            compiled=compiled,
            commit=True,
        )
        return ContextSnapshotRead(
            id=snapshot.id,
            project_id=snapshot.project_id,
            role=snapshot.role,
            task=snapshot.task,
            content_md=snapshot.content_md,
            token_count=snapshot.token_count,
            project_state_version=snapshot.project_state_version,
            created_at=snapshot.created_at,
        )

    # Ephemeral response (not persisted)
    return ContextSnapshotRead(
        id=uuid.uuid4(),
        project_id=project.id,
        role=payload.role,
        task=payload.task,
        content_md=compiled.content_md,
        token_count=compiled.token_count,
        project_state_version=compiled.state_version,
        created_at=datetime.now(timezone.utc),
    )
