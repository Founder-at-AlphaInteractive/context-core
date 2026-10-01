"""Project API routes."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services import projects as projects_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> ProjectRead:
    project = projects_service.create_project(
        db, name=payload.name, description=payload.description
    )
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
def list_projects(
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> list[ProjectRead]:
    items = projects_service.list_projects(db)
    return [ProjectRead.model_validate(p) for p in items]


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ProjectRead:
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ProjectRead:
    raw = payload.model_dump(exclude_unset=True)
    clear_description = "description" in raw and raw["description"] is None

    updated = projects_service.update_project(
        db,
        project,
        name=payload.name,
        description=payload.description,
        clear_description=clear_description,
    )
    return ProjectRead.model_validate(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> None:
    projects_service.delete_project(db, project)
