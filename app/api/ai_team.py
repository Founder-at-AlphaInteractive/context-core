"""AI profile + project AI team routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.ai_profile import ProjectAIRole
from app.models.device import Device
from app.models.project import Project
from app.schemas.ai_profile import (
    AIProfileCreate,
    AIProfileRead,
    ProjectAIRoleCreate,
    ProjectAIRoleRead,
    ProjectAIRoleWithProfile,
)
from app.services import ai_team as ai_team_service
from app.services.ai_team import (
    AIProfileNameConflict,
    AIProfileNotFound,
    ProjectRoleConflict,
)

router = APIRouter(tags=["ai-team"])


# ---------------------------------------------------------------- profiles


@router.post(
    "/ai-profiles",
    response_model=AIProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_ai_profile(
    payload: AIProfileCreate,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> AIProfileRead:
    try:
        profile = ai_team_service.create_ai_profile(
            db,
            name=payload.name,
            provider=payload.provider,
            type=payload.type,
            detection_hints=payload.detection_hints,
            permissions=payload.permissions,
        )
    except AIProfileNameConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AI profile with name '{payload.name}' already exists",
        )
    return AIProfileRead.model_validate(profile)


@router.get("/ai-profiles", response_model=list[AIProfileRead])
def list_ai_profiles(
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> list[AIProfileRead]:
    return [
        AIProfileRead.model_validate(p) for p in ai_team_service.list_ai_profiles(db)
    ]


@router.get("/ai-profiles/{profile_id}", response_model=AIProfileRead)
def read_ai_profile(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> AIProfileRead:
    profile = ai_team_service.get_ai_profile(db, profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AI profile not found"
        )
    return AIProfileRead.model_validate(profile)


# --------------------------------------------------------------- team


@router.post(
    "/projects/{project_id}/ai-team",
    response_model=ProjectAIRoleWithProfile,
    status_code=status.HTTP_201_CREATED,
)
def assign_ai_to_project(
    payload: ProjectAIRoleCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> ProjectAIRoleWithProfile:
    try:
        role = ai_team_service.assign_role(
            db,
            project_id=project.id,
            ai_profile_id=payload.ai_profile_id,
            role_name=payload.role_name,
            permissions=payload.permissions,
        )
    except AIProfileNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AI profile not found"
        )
    except ProjectRoleConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )

    profile = ai_team_service.get_ai_profile(db, payload.ai_profile_id)
    return ProjectAIRoleWithProfile(
        **ProjectAIRoleRead.model_validate(role).model_dump(),
        ai_profile=AIProfileRead.model_validate(profile),
    )


@router.get(
    "/projects/{project_id}/ai-team",
    response_model=list[ProjectAIRoleWithProfile],
)
def list_project_team(
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> list[ProjectAIRoleWithProfile]:
    roles = ai_team_service.list_roles(db, project.id)
    out: list[ProjectAIRoleWithProfile] = []
    for role in roles:
        if role.ai_profile is None:
            continue
        out.append(
            ProjectAIRoleWithProfile(
                **ProjectAIRoleRead.model_validate(role).model_dump(),
                ai_profile=AIProfileRead.model_validate(role.ai_profile),
            )
        )
    return out


@router.delete(
    "/projects/{project_id}/ai-team/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_project_team_role(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> None:
    role: ProjectAIRole | None = ai_team_service.get_role(db, role_id)
    if role is None or role.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
        )
    ai_team_service.remove_role(db, role)
