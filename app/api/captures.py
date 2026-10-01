"""Capture + conversation API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.capture import CaptureRead, CaptureRequest, CaptureResult
from app.schemas.conversation import (
    ConversationRead,
    ConversationWithMessages,
    MessageRead,
)
from app.services import captures as captures_service

router = APIRouter(prefix="/projects/{project_id}", tags=["captures"])


@router.post(
    "/captures",
    response_model=CaptureResult,
    status_code=status.HTTP_201_CREATED,
)
def create_capture(
    payload: CaptureRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> CaptureResult:
    # Authenticated Device.id is authoritative
    if payload.client_id is not None and payload.client_id != device.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="client_id does not match authenticated device",
        )

    try:
        outcome = captures_service.capture_conversation(
            db,
            project_id=project.id,
            client_id=device.id,
            payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    return CaptureResult(
        capture=CaptureRead.model_validate(outcome.capture),
        conversation_id=outcome.conversation.id,
        messages_inserted=outcome.messages_inserted,
        messages_deduplicated=outcome.messages_deduplicated,
        idempotent_replay=outcome.idempotent_replay,
    )


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> list[ConversationRead]:
    items = captures_service.list_conversations(db, project.id, limit=limit)
    return [ConversationRead.model_validate(c) for c in items]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationWithMessages,
)
def read_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ConversationWithMessages:
    conv = captures_service.get_conversation(db, project.id, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    messages = captures_service.list_messages(db, project.id, conv.id)
    return ConversationWithMessages(
        **ConversationRead.model_validate(conv).model_dump(),
        messages=[MessageRead.model_validate(m) for m in messages],
    )
