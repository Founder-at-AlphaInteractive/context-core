"""Deterministic conflict detection.

We do NOT rely on an LLM to detect contradictions. Rules:

1. Same key, different value among ACTIVE / PROPOSED items of the same type
   within the same project -> contradiction.
2. Proposed TASK whose key matches a COMPLETED TASK key -> duplicate work.
3. New item with weaker provenance trying to overwrite same-key active item
   -> weak override warning.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    PROVENANCE_RANK,
    MemoryStatus,
    MemoryType,
)
from app.models.memory import MemoryItem
from app.schemas.memory import ConflictRead

CONTRADICTION_TYPES = {
    MemoryType.DECISION,
    MemoryType.REQUIREMENT,
    MemoryType.FACT,
    MemoryType.PROBLEM,
}


def _existing_active_for_key(
    db: Session,
    *,
    project_id: uuid.UUID,
    memory_type: MemoryType,
    key: str,
    exclude_id: uuid.UUID | None = None,
) -> list[MemoryItem]:
    stmt = (
        select(MemoryItem)
        .where(
            MemoryItem.project_id == project_id,
            MemoryItem.type == memory_type,
            MemoryItem.key == key,
            MemoryItem.status.in_(
                [MemoryStatus.ACTIVE, MemoryStatus.PROPOSED]
            ),
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(MemoryItem.id != exclude_id)
    return list(db.execute(stmt).scalars())


def detect_conflicts(
    db: Session,
    *,
    project_id: uuid.UUID,
    candidate: MemoryItem,
) -> list[ConflictRead]:
    """Pure, side-effect-free conflict detection engine."""
    conflicts: list[ConflictRead] = []

    # Rule 1: same key + same type + different value -> contradiction.
    if candidate.key and candidate.type in CONTRADICTION_TYPES:
        existing = _existing_active_for_key(
            db,
            project_id=project_id,
            memory_type=candidate.type,
            key=candidate.key,
            exclude_id=candidate.id,
        )
        for other in existing:
            if (other.value or "").strip() == (candidate.value or "").strip():
                continue
            conflicts.append(
                ConflictRead(
                    kind=f"{candidate.type.value}_contradiction",
                    message=(
                        f"Existing {candidate.type.value} '{candidate.key}' = "
                        f"'{other.value}' conflicts with new value "
                        f"'{candidate.value}'."
                    ),
                    existing_memory_id=other.id,
                    proposed_memory_id=candidate.id,
                    key=candidate.key,
                    existing_value=other.value,
                    proposed_value=candidate.value,
                )
            )

    # Rule 2: proposed task matches a completed task key -> duplicate work.
    if candidate.type == MemoryType.TASK and candidate.key:
        completed = list(
            db.execute(
                select(MemoryItem).where(
                    MemoryItem.project_id == project_id,
                    MemoryItem.type == MemoryType.TASK,
                    MemoryItem.key == candidate.key,
                    MemoryItem.status == MemoryStatus.COMPLETED,
                    MemoryItem.id != candidate.id,
                )
            ).scalars()
        )
        for done in completed:
            conflicts.append(
                ConflictRead(
                    kind="duplicate_completed_task",
                    message=(
                        f"Task '{candidate.key}' was already completed; new task "
                        f"may duplicate existing work."
                    ),
                    existing_memory_id=done.id,
                    proposed_memory_id=candidate.id,
                    key=candidate.key,
                )
            )

    # Rule 3: weaker provenance trying to override stronger same-key item.
    if candidate.key:
        existing = _existing_active_for_key(
            db,
            project_id=project_id,
            memory_type=candidate.type,
            key=candidate.key,
            exclude_id=candidate.id,
        )
        cand_rank = PROVENANCE_RANK.get(candidate.provenance, 0)
        for other in existing:
            other_rank = PROVENANCE_RANK.get(other.provenance, 0)
            if other_rank > cand_rank:
                conflicts.append(
                    ConflictRead(
                        kind="provenance_override",
                        message=(
                            f"New item has weaker provenance "
                            f"({candidate.provenance.value}) than existing "
                            f"({other.provenance.value}) for key "
                            f"'{candidate.key}'."
                        ),
                        existing_memory_id=other.id,
                        proposed_memory_id=candidate.id,
                        key=candidate.key,
                        existing_value=other.value,
                        proposed_value=candidate.value,
                    )
                )

    return conflicts
