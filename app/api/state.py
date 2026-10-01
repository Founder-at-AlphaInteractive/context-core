"""Project state API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.state import ProjectStateRead, ProjectStateUpdate
from app.services import state as state_service
from app.services.state import StateVersionConflict

router = APIRouter(prefix="/projects/{project_id}/state", tags=["state"])


@router.get("", response_model=ProjectStateRead | None)
def read_current_state(
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ProjectStateRead | None:
    current = state_service.get_current_state(db, project.id)
    if current is None:
        return None
    return ProjectStateRead.model_validate(current)


@router.get("/history", response_model=list[ProjectStateRead])
def read_state_history(
    limit: int = 50,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> list[ProjectStateRead]:
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 500",
        )
    history = state_service.list_state_history(db, project.id, limit=limit)
    return [ProjectStateRead.model_validate(s) for s in history]


@router.put("", response_model=ProjectStateRead)
def update_state(
    payload: ProjectStateUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ProjectStateRead:
    try:
        new_state = state_service.update_state(
            db,
            project=project,
            device=device,
            patch=payload.patch,
            expected_version=payload.expected_version,
            note=payload.note,
            replace=payload.replace,
        )
    except StateVersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "State version conflict",
                "expected": exc.expected,
                "actual": exc.actual,
            },
        )
    return ProjectStateRead.model_validate(new_state)
