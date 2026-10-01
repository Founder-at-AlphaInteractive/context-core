"""Memory item services.

Memory items are the structured representation of project reality
(facts, requirements, decisions, proposals, tasks, problems).

Authority and Promotion rules:
- Human authority outranks AI authority.
- Items with AI/weaker provenance (AI_PROPOSAL, AI_EXTRACTED, INFERENCE, UNKNOWN)
  MUST start in PROPOSED status and cannot directly claim ACTIVE status.
- Authoritative items (USER_CONFIRMED, PROJECT_FILE, etc.) start in ACTIVE status.
- Authoritative ACTIVE items cannot be downgraded to PROPOSED or overwritten by weaker proposals.
- Supersession requires target existence in the same project and sufficient provenance rank.
- Events contain minimal synchronization metadata (no full text/secrets).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    PROVENANCE_RANK,
    EventType,
    MemoryStatus,
    MemoryType,
    Provenance,
)
from app.models.memory import MemoryItem
from app.schemas.memory import (
    ConflictRead,
    MemoryItemCreate,
    MemoryItemUpdate,
)
from app.services.conflicts import detect_conflicts
from app.services.events import emit_event

_EVENT_BY_TYPE = {
    MemoryType.DECISION: (EventType.DECISION_CREATED, EventType.DECISION_UPDATED),
    MemoryType.TASK: (EventType.TASK_CREATED, EventType.TASK_UPDATED),
    MemoryType.PROBLEM: (EventType.PROBLEM_CREATED, EventType.PROBLEM_UPDATED),
}

AUTHORITATIVE_PROVENANCES = {
    Provenance.USER_CONFIRMED,
    Provenance.CONFIRMED_DECISION,
    Provenance.PROJECT_FILE,
    Provenance.TEST_RESULT,
    Provenance.DOCUMENTATION,
}

AI_WEAK_PROVENANCES = {
    Provenance.AI_EXTRACTED,
    Provenance.AI_PROPOSAL,
    Provenance.INFERENCE,
    Provenance.UNKNOWN,
}


def _event_types_for(memory_type: MemoryType) -> tuple[EventType, EventType]:
    return _EVENT_BY_TYPE.get(
        memory_type, (EventType.MEMORY_CREATED, EventType.MEMORY_UPDATED)
    )


def list_memory(
    db: Session,
    project_id: uuid.UUID,
    *,
    memory_type: MemoryType | None = None,
    status: MemoryStatus | None = None,
    limit: int = 500,
) -> list[MemoryItem]:
    stmt = select(MemoryItem).where(MemoryItem.project_id == project_id)
    if memory_type is not None:
        stmt = stmt.where(MemoryItem.type == memory_type)
    if status is not None:
        stmt = stmt.where(MemoryItem.status == status)
    stmt = stmt.order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def get_memory(
    db: Session, project_id: uuid.UUID, memory_id: uuid.UUID
) -> MemoryItem | None:
    item = db.get(MemoryItem, memory_id)
    if item is None or item.project_id != project_id:
        return None
    return item


def _supersede_if_needed(
    db: Session,
    *,
    project_id: uuid.UUID,
    supersedes_id: uuid.UUID,
    replacement_provenance: Provenance,
) -> MemoryItem:
    target = db.get(MemoryItem, supersedes_id)
    if target is None:
        raise ValueError("Target memory item for supersession not found")
    if target.project_id != project_id:
        raise ValueError("Cannot supersede memory item from a different project")

    # Authoritative memory protection: weaker provenance cannot supersede stronger provenance
    target_rank = PROVENANCE_RANK.get(target.provenance, 0)
    rep_rank = PROVENANCE_RANK.get(replacement_provenance, 0)
    if rep_rank < target_rank:
        raise ValueError(
            f"Cannot supersede memory item with higher authority "
            f"({target.provenance.value}) using weaker provenance ({replacement_provenance.value})"
        )

    if target.status not in (MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED):
        target.status = MemoryStatus.SUPERSEDED
        db.flush()
    return target


def create_memory(
    db: Session, *, project_id: uuid.UUID, payload: MemoryItemCreate
) -> tuple[MemoryItem, list[ConflictRead]]:
    # Authority enforcement: AI proposal/extraction cannot manufacture ACTIVE authority
    if payload.provenance in AI_WEAK_PROVENANCES:
        if payload.status == MemoryStatus.ACTIVE:
            raise ValueError(
                f"Memory item with provenance '{payload.provenance.value}' "
                "cannot be created directly in ACTIVE status"
            )
        status = payload.status if payload.status is not None else MemoryStatus.PROPOSED
    else:
        status = payload.status if payload.status is not None else MemoryStatus.ACTIVE

    # Validate supersession target if specified
    if payload.supersedes_id is not None:
        _supersede_if_needed(
            db,
            project_id=project_id,
            supersedes_id=payload.supersedes_id,
            replacement_provenance=payload.provenance,
        )

    item = MemoryItem(
        project_id=project_id,
        type=payload.type,
        status=status,
        provenance=payload.provenance,
        title=payload.title,
        content=payload.content,
        key=payload.key,
        value=payload.value,
        confidence=payload.confidence,
        tags=list(payload.tags or []),
        source_capture_id=payload.source_capture_id,
        source_message_id=payload.source_message_id,
        supersedes_id=payload.supersedes_id,
    )
    db.add(item)
    db.flush()

    conflicts = detect_conflicts(db, project_id=project_id, candidate=item)

    created_event, _ = _event_types_for(payload.type)
    emit_event(
        db,
        project_id=project_id,
        event_type=created_event,
        entity_id=item.id,
        payload={
            "memory_id": str(item.id),
            "type": payload.type.value,
            "status": item.status.value,
            "provenance": item.provenance.value,
            "key": item.key,
            "conflict_count": len(conflicts),
            "supersedes_id": str(item.supersedes_id) if item.supersedes_id else None,
        },
    )

    db.commit()
    db.refresh(item)
    return item, conflicts


def update_memory(
    db: Session,
    *,
    project_id: uuid.UUID,
    item: MemoryItem,
    payload: MemoryItemUpdate,
) -> tuple[MemoryItem, list[ConflictRead]]:
    # Lock row to prevent lost-update races
    locked_item = db.execute(
        select(MemoryItem).where(MemoryItem.id == item.id).with_for_update()
    ).scalar_one()

    changed: dict = {}

    if payload.title is not None and payload.title != locked_item.title:
        locked_item.title = payload.title
        changed["title"] = payload.title
    if payload.content is not None and payload.content != locked_item.content:
        locked_item.content = payload.content
        changed["content"] = True
    if payload.key is not None and payload.key != locked_item.key:
        locked_item.key = payload.key
        changed["key"] = payload.key
    if payload.value is not None and payload.value != locked_item.value:
        locked_item.value = payload.value
        changed["value"] = True
    if payload.confidence is not None and payload.confidence != locked_item.confidence:
        locked_item.confidence = payload.confidence
        changed["confidence"] = payload.confidence
    if payload.tags is not None and list(payload.tags) != list(locked_item.tags or []):
        locked_item.tags = list(payload.tags)
        changed["tags"] = list(payload.tags)

    # 1. Provenance mutation rules
    if payload.provenance is not None and payload.provenance != locked_item.provenance:
        current_rank = PROVENANCE_RANK.get(locked_item.provenance, 0)
        new_rank = PROVENANCE_RANK.get(payload.provenance, 0)
        if new_rank < current_rank:
            raise ValueError(
                f"Cannot downgrade memory provenance from {locked_item.provenance.value} "
                f"to {payload.provenance.value}"
            )
        locked_item.provenance = payload.provenance
        changed["provenance"] = payload.provenance.value

    # 2. Status transition validation rules
    if payload.status is not None and payload.status != locked_item.status:
        # ACTIVE authoritative memory cannot be downgraded to PROPOSED
        if locked_item.status == MemoryStatus.ACTIVE and payload.status == MemoryStatus.PROPOSED:
            raise ValueError("Authoritative ACTIVE memory cannot be downgraded to PROPOSED")

        # Memory with AI/weaker provenance cannot be transitioned to ACTIVE without user confirmation
        eff_provenance = payload.provenance or locked_item.provenance
        if payload.status == MemoryStatus.ACTIVE and eff_provenance in AI_WEAK_PROVENANCES:
            raise ValueError(
                f"Cannot promote memory with provenance '{eff_provenance.value}' "
                "to ACTIVE without user confirmation"
            )

        # Terminal state: SUPERSEDED cannot be reactivated
        if locked_item.status == MemoryStatus.SUPERSEDED and payload.status in (
            MemoryStatus.ACTIVE,
            MemoryStatus.PROPOSED,
        ):
            raise ValueError("SUPERSEDED memory cannot be reactivated")

        # Terminal state: ARCHIVED cannot be reactivated
        if locked_item.status == MemoryStatus.ARCHIVED and payload.status in (
            MemoryStatus.ACTIVE,
            MemoryStatus.PROPOSED,
        ):
            raise ValueError("ARCHIVED memory cannot be reactivated")

        locked_item.status = payload.status
        changed["status"] = payload.status.value

    conflicts: list[ConflictRead] = []
    if changed:
        db.flush()
        conflicts = detect_conflicts(db, project_id=project_id, candidate=locked_item)

        _, updated_event = _event_types_for(locked_item.type)
        emit_event(
            db,
            project_id=project_id,
            event_type=updated_event,
            entity_id=locked_item.id,
            payload={
                "memory_id": str(locked_item.id),
                "type": locked_item.type.value,
                "fields_changed": list(changed.keys()),
                "status": locked_item.status.value,
                "provenance": locked_item.provenance.value,
                "key": locked_item.key,
                "conflict_count": len(conflicts),
            },
        )

    db.commit()
    db.refresh(locked_item)
    return locked_item, conflicts
