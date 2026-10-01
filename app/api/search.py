"""Search API route."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.conversation import ConversationRead, MessageRead
from app.schemas.handoff import HandoffRead
from app.schemas.memory import MemoryItemRead
from app.services import search as search_service

router = APIRouter(prefix="/projects/{project_id}/search", tags=["search"])


class SearchResult(BaseModel):
    memory: list[MemoryItemRead]
    messages: list[MessageRead]
    conversations: list[ConversationRead]
    handoffs: list[HandoffRead]


@router.get("", response_model=SearchResult)
def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> SearchResult:
    try:
        hits = search_service.search_project(
            db, project_id=project.id, query=q, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return SearchResult(
        memory=[MemoryItemRead.model_validate(m) for m in hits["memory"]],
        messages=[MessageRead.model_validate(m) for m in hits["messages"]],
        conversations=[
            ConversationRead.model_validate(c) for c in hits["conversations"]
        ],
        handoffs=[HandoffRead.model_validate(h) for h in hits["handoffs"]],
    )
