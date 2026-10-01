"""Project state services.

Project state is versioned. Updates create a NEW ProjectState row; the
previous row is retained as history.

Deterministic optimistic concurrency:
- Serializes under a row lock on the project to prevent concurrent version collisions.
- When `expected_version` is supplied, rejects stale updates with StateVersionConflict.
- Uses deep copy and recursive merge so historical state rows are strictly immutable.
"""

import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.enums import EventType
from app.models.project import Project, ProjectState
from app.services.events import emit_event


class StateVersionConflict(Exception):
    def __init__(self, expected: int | None, actual: int) -> None:
        super().__init__(f"State version conflict: expected={expected} actual={actual}")
        self.expected = expected
        self.actual = actual


def _deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge `patch` into an immutable copy of `base`.

    - dict + dict -> merged dict (patch wins on scalar conflicts)
    - list + list -> concatenation (dedup on exact match)
    - otherwise -> patch replaces base
    """
    if isinstance(base, dict) and isinstance(patch, dict):
        result = copy.deepcopy(base)
        for key, value in patch.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    if isinstance(base, list) and isinstance(patch, list):
        merged: list = copy.deepcopy(base)
        for item in patch:
            if item not in merged:
                merged.append(copy.deepcopy(item))
        return merged

    return copy.deepcopy(patch)


def get_current_state(
    db: Session, project_id: uuid.UUID
) -> ProjectState | None:
    return db.execute(
        select(ProjectState)
        .where(ProjectState.project_id == project_id)
        .order_by(ProjectState.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_state_history(
    db: Session, project_id: uuid.UUID, limit: int = 50
) -> list[ProjectState]:
    return list(
        db.execute(
            select(ProjectState)
            .where(ProjectState.project_id == project_id)
            .order_by(ProjectState.version.desc())
            .limit(limit)
        ).scalars()
    )


def update_state(
    db: Session,
    *,
    project: Project,
    device: Device,
    patch: dict,
    expected_version: int | None = None,
    note: str | None = None,
    replace: bool = False,
) -> ProjectState:
    # Row lock on the project to serialize version allocation and prevent concurrent race conditions
    locked_project = db.execute(
        select(Project).where(Project.id == project.id).with_for_update()
    ).scalar_one_or_none()

    if locked_project is None:
        raise ValueError(f"Project {project.id} not found")

    current = get_current_state(db, project.id)
    current_version = current.version if current is not None else 0

    if expected_version is not None and expected_version != current_version:
        raise StateVersionConflict(expected=expected_version, actual=current_version)

    if replace:
        new_state = copy.deepcopy(patch)
    else:
        base = current.state_json if current is not None else {}
        new_state = _deep_merge(base, patch)

    new_version = current_version + 1
    state = ProjectState(
        project_id=project.id,
        version=new_version,
        state_json=new_state,
        created_by_device_id=device.id,
        note=note,
    )
    db.add(state)
    db.flush()

    emit_event(
        db,
        project_id=project.id,
        event_type=EventType.PROJECT_STATE_CHANGED,
        entity_id=state.id,
        payload={
            "version": new_version,
            "note": note,
            "replace": replace,
        },
    )
    db.commit()
    db.refresh(state)
    return state
