"""Project lifecycle services."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EventType
from app.models.project import Project
from app.services.events import emit_event


def create_project(
    db: Session, *, name: str, description: str | None = None
) -> Project:
    project = Project(name=name, description=description)
    db.add(project)
    db.flush()  # populates project.id

    emit_event(
        db,
        project_id=project.id,
        event_type=EventType.PROJECT_CREATED,
        entity_id=project.id,
        payload={"name": name, "description": description},
    )
    db.commit()
    db.refresh(project)
    return project


def list_projects(db: Session) -> list[Project]:
    return list(
        db.execute(select(Project).order_by(Project.created_at.desc())).scalars()
    )


def get_project(db: Session, project_id: uuid.UUID) -> Project | None:
    return db.get(Project, project_id)


def update_project(
    db: Session,
    project: Project,
    *,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
) -> Project:
    changed: dict = {}
    if name is not None and name != project.name:
        project.name = name
        changed["name"] = name
    if description is not None:
        if project.description != description:
            project.description = description
            changed["description"] = description
    elif clear_description:
        if project.description is not None:
            project.description = None
            changed["description"] = None

    if changed:
        emit_event(
            db,
            project_id=project.id,
            event_type=EventType.PROJECT_UPDATED,
            entity_id=project.id,
            payload=changed,
        )
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    """Hard delete project and cascade delete all child records."""
    db.delete(project)
    db.commit()
