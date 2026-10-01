"""AI profile and project-role services."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.ai_profile import AIProfile, ProjectAIRole
from app.models.enums import AIProfileType, EventType
from app.services.events import emit_event


class AIProfileNameConflict(Exception):
    pass


class AIProfileNotFound(Exception):
    pass


class ProjectRoleConflict(Exception):
    pass


def create_ai_profile(
    db: Session,
    *,
    name: str,
    provider: str,
    type: AIProfileType,
    detection_hints: dict | None = None,
    permissions: dict | None = None,
) -> AIProfile:
    profile = AIProfile(
        name=name,
        provider=provider,
        type=type,
        detection_hints=detection_hints or {},
        permissions=permissions or {},
    )
    try:
        with db.begin_nested():
            db.add(profile)
            db.flush()
    except IntegrityError as exc:
        raise AIProfileNameConflict(f"AI profile with name '{name}' already exists") from exc

    db.commit()
    db.refresh(profile)
    return profile


def list_ai_profiles(db: Session) -> list[AIProfile]:
    return list(
        db.execute(select(AIProfile).order_by(AIProfile.name.asc())).scalars()
    )


def get_ai_profile(db: Session, profile_id: uuid.UUID) -> AIProfile | None:
    return db.get(AIProfile, profile_id)


def assign_role(
    db: Session,
    *,
    project_id: uuid.UUID,
    ai_profile_id: uuid.UUID,
    role_name: str,
    permissions: dict | None = None,
) -> ProjectAIRole:
    profile = db.get(AIProfile, ai_profile_id)
    if profile is None:
        raise AIProfileNotFound(f"AI profile {ai_profile_id} not found")

    role = ProjectAIRole(
        project_id=project_id,
        ai_profile_id=ai_profile_id,
        role_name=role_name,
        permissions=permissions or {},
    )
    try:
        with db.begin_nested():
            db.add(role)
            db.flush()
    except IntegrityError as exc:
        raise ProjectRoleConflict(
            f"Role '{role_name}' already assigned to this AI in this project"
        ) from exc


    emit_event(
        db,
        project_id=project_id,
        event_type=EventType.AI_TEAM_UPDATED,
        entity_id=role.id,
        payload={
            "action": "assigned",
            "ai_profile_id": str(ai_profile_id),
            "role_name": role_name,
        },
    )
    db.commit()
    db.refresh(role)
    return role


def list_roles(db: Session, project_id: uuid.UUID) -> list[ProjectAIRole]:
    """Retrieve roles for a project with profiles eagerly loaded to prevent N+1 queries."""
    return list(
        db.execute(
            select(ProjectAIRole)
            .options(joinedload(ProjectAIRole.ai_profile))
            .where(ProjectAIRole.project_id == project_id)
            .order_by(ProjectAIRole.created_at.asc())
        ).scalars()
    )


def get_role(db: Session, role_id: uuid.UUID) -> ProjectAIRole | None:
    return db.get(ProjectAIRole, role_id)


def remove_role(db: Session, role: ProjectAIRole) -> None:
    project_id = role.project_id
    role_id = role.id
    role_name = role.role_name
    ai_profile_id = role.ai_profile_id

    db.delete(role)
    emit_event(
        db,
        project_id=project_id,
        event_type=EventType.AI_TEAM_UPDATED,
        entity_id=role_id,
        payload={
            "action": "removed",
            "ai_profile_id": str(ai_profile_id),
            "role_name": role_name,
        },
    )
    db.commit()
