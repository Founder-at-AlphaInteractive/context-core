"""Handoff service.

A handoff packages the minimal context needed for the next AI role to
continue work.

Invariants:
- Atomic compound transaction: snapshot creation + handoff creation + HANDOFF_CREATED
  event are committed together. Failure leaves no orphaned snapshot.
- Strict project validation: verifies AI profiles exist and preserves project isolation.
- Token budget scope: token_budget strictly constrains the compiled context component,
  while handoff envelope metadata provides top-level routing directives.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_profile import AIProfile, ProjectAIRole
from app.models.enums import EventType, MemoryStatus, MemoryType
from app.models.handoff import Handoff
from app.models.project import Project
from app.services import context_compiler, memory as memory_service
from app.services.events import emit_event


def _items(memory_items, t: MemoryType, statuses):
    return [i for i in memory_items if i.type == t and i.status in statuses]


def generate_handoff(
    db: Session,
    *,
    project: Project,
    to_role: str,
    to_ai_profile_id: uuid.UUID | None,
    from_role: str | None,
    from_ai_profile_id: uuid.UUID | None,
    topic: str,
    task: str | None,
    token_budget: int | None,
    client_id: uuid.UUID | None = None,
) -> Handoff:
    # 1. Validation: AI profiles and project roles
    if to_ai_profile_id is not None:
        to_profile = db.get(AIProfile, to_ai_profile_id)
        if to_profile is None:
            raise ValueError(f"Target AI Profile {to_ai_profile_id} not found")

        # Project isolation: verify profile is not bound to another project
        other_project_role = db.execute(
            select(ProjectAIRole).where(
                ProjectAIRole.ai_profile_id == to_ai_profile_id,
                ProjectAIRole.project_id != project.id,
            )
        ).scalar_one_or_none()
        if other_project_role is not None:
            raise ValueError("Target AI Profile belongs to a different project")

    if from_ai_profile_id is not None:
        from_profile = db.get(AIProfile, from_ai_profile_id)
        if from_profile is None:
            raise ValueError(f"Source AI Profile {from_ai_profile_id} not found")

        other_from_role = db.execute(
            select(ProjectAIRole).where(
                ProjectAIRole.ai_profile_id == from_ai_profile_id,
                ProjectAIRole.project_id != project.id,
            )
        ).scalar_one_or_none()
        if other_from_role is not None:
            raise ValueError("Source AI Profile belongs to a different project")

    # 2. Compile context (budget strictly applies to compiled context)
    compiled = context_compiler.compile_context(
        db,
        project=project,
        role=to_role,
        task=task,
        token_budget=token_budget,
        include_recent_messages=20,
    )

    # 3. Create snapshot without committing independently (atomic compound transaction)
    snapshot = context_compiler.persist_snapshot(
        db,
        project_id=project.id,
        role=to_role,
        task=task,
        compiled=compiled,
        commit=False,
    )

    memory_items = memory_service.list_memory(db, project.id, limit=500)
    decisions = _items(memory_items, MemoryType.DECISION, [MemoryStatus.ACTIVE])
    requirements = _items(memory_items, MemoryType.REQUIREMENT, [MemoryStatus.ACTIVE])
    open_tasks = _items(
        memory_items, MemoryType.TASK, [MemoryStatus.ACTIVE, MemoryStatus.PROPOSED]
    )
    problems = _items(
        memory_items, MemoryType.PROBLEM, [MemoryStatus.ACTIVE, MemoryStatus.PROPOSED]
    )

    lines: list[str] = []
    lines.append(f"# Handoff — {topic}")
    lines.append(f"**Project:** {project.name}")
    if from_role:
        lines.append(f"**From:** {from_role}")
    lines.append(f"**To:** {to_role}")
    if task:
        lines.append(f"**Task:** {task}")
    lines.append("")

    lines.append("## Objective")
    lines.append(compiled.sections.get("objective", "(no objective recorded)"))
    lines.append("")

    lines.append("## Current Reality")
    lines.append(compiled.sections.get("current_state", "(no state)"))
    lines.append("")

    lines.append("## Active Decisions")
    if decisions:
        for d in decisions:
            lines.append(f"- {d.title}: {d.content}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Requirements")
    if requirements:
        for r in requirements:
            lines.append(f"- {r.title}: {r.content}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Open Tasks")
    if open_tasks:
        for t in open_tasks:
            lines.append(f"- {t.title}: {t.content}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Problems")
    if problems:
        for p in problems:
            lines.append(f"- {p.title}: {p.content}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Required Context")
    lines.append(compiled.content_md)
    lines.append("")
    lines.append("## Next Task")
    lines.append(task or "(specify explicitly)")

    content_md = "\n".join(lines)

    handoff = Handoff(
        project_id=project.id,
        from_ai_profile_id=from_ai_profile_id,
        from_role=from_role,
        to_ai_profile_id=to_ai_profile_id,
        to_role=to_role,
        topic=topic,
        content_md=content_md,
        context_snapshot_id=snapshot.id,
    )
    db.add(handoff)
    db.flush()

    emit_event(
        db,
        project_id=project.id,
        event_type=EventType.HANDOFF_CREATED,
        entity_id=handoff.id,
        payload={
            "to_role": to_role,
            "from_role": from_role,
            "topic": topic,
            "snapshot_id": str(snapshot.id),
        },
        client_id=client_id,
    )

    db.commit()
    db.refresh(handoff)
    db.refresh(snapshot)
    return handoff


def list_handoffs(
    db: Session, project_id: uuid.UUID, limit: int = 100
) -> list[Handoff]:
    return list(
        db.execute(
            select(Handoff)
            .where(Handoff.project_id == project_id)
            .order_by(Handoff.created_at.desc(), Handoff.id.desc())
            .limit(limit)
        ).scalars()
    )


def get_handoff(
    db: Session, project_id: uuid.UUID, handoff_id: uuid.UUID
) -> Handoff | None:
    handoff = db.get(Handoff, handoff_id)
    if handoff is None or handoff.project_id != project_id:
        return None
    return handoff
