"""COMBINE API route."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.combine import CombineRequest, CombineResult
from app.services import combine as combine_service

router = APIRouter(prefix="/projects/{project_id}/combine", tags=["combine"])


@router.post("", response_model=CombineResult)
def combine(
    payload: CombineRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> CombineResult:
    return combine_service.combine_project(
        db,
        project=project,
        task=payload.task,
        target_role=payload.target_role,
        include_recent_messages=payload.include_recent_messages,
    )
