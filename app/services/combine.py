"""COMBINE operation.

Analyzes current project reality and produces a structured synthesis:
what is established, what is active, what is blocked, what conflicts,
open questions, and a recommended next step.

Invariants:
- Deterministic project-continuity operation (not a generic conversation summarizer).
- Pure read operation: side-effect free, does NOT mutate project reality or state.
- Target role precedence: a caller-specified target_role is honored with precedence,
  while automated routing heuristics provide suggestions when omitted.
- include_recent_messages parameter is respected and integrated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Message
from app.models.enums import MemoryStatus, MemoryType
from app.models.project import Project
from app.schemas.combine import (
    CombineNextStep,
    CombineResult,
)
from app.schemas.memory import ConflictRead
from app.services import memory as memory_service
from app.services import state as state_service
from app.services.conflicts import detect_conflicts


def _conflicts_for_project(
    db: Session, project_id: uuid.UUID
) -> list[ConflictRead]:
    """Scan active + proposed memory items and return fresh conflicts deterministically."""
    conflicts: list[ConflictRead] = []
    items = memory_service.list_memory(
        db,
        project_id,
        status=None,
        limit=1000,
    )
    seen_keys: set[tuple[str, str]] = set()
    for item in items:
        if item.status not in (MemoryStatus.ACTIVE, MemoryStatus.PROPOSED):
            continue
        if not item.key:
            continue
        dedup_key = (item.type.value, item.key)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        for c in detect_conflicts(db, project_id=project_id, candidate=item):
            # Skip self-conflicts
            if c.existing_memory_id == item.id and c.proposed_memory_id == item.id:
                continue
            conflicts.append(c)
    return conflicts


def _heuristic_pick_role(
    *, blocked: list[str], open_tasks: list[str], problems: list[str]
) -> tuple[str, str]:
    """Deterministic routing heuristic when caller does not specify a target role."""
    if problems:
        return "Technical Advisor", "Unresolved problems remain requiring technical diagnosis."
    if blocked:
        return "Architecture", "Project has blocked items; architectural alignment needed."
    if open_tasks:
        return "Implementation Agent", "Discrete open tasks are ready for execution."
    return "Coding Architect", "No immediate blockers or tasks; ready for architectural planning."


def combine_project(
    db: Session,
    *,
    project: Project,
    task: str | None,
    target_role: str | None,
    include_recent_messages: int = 20,
) -> CombineResult:
    state = state_service.get_current_state(db, project.id)
    state_json = dict(state.state_json) if state is not None else {}

    active = list(state_json.get("active") or [])
    completed = list(state_json.get("completed") or [])
    blocked = list(state_json.get("blocked") or [])

    memory_items = memory_service.list_memory(db, project.id, limit=1000)

    decisions = [
        i for i in memory_items if i.type == MemoryType.DECISION and i.status == MemoryStatus.ACTIVE
    ]
    requirements = [
        i for i in memory_items if i.type == MemoryType.REQUIREMENT and i.status == MemoryStatus.ACTIVE
    ]
    open_tasks = [
        i for i in memory_items
        if i.type == MemoryType.TASK and i.status in (MemoryStatus.ACTIVE, MemoryStatus.PROPOSED)
    ]
    problems = [
        i for i in memory_items
        if i.type == MemoryType.PROBLEM and i.status in (MemoryStatus.ACTIVE, MemoryStatus.PROPOSED)
    ]
    proposals = [
        i for i in memory_items
        if i.type == MemoryType.PROPOSAL and i.status in (MemoryStatus.ACTIVE, MemoryStatus.PROPOSED)
    ]

    conflicts = _conflicts_for_project(db, project.id)

    # 1. Load recent messages if requested
    recent_messages: list[Message] = []
    if include_recent_messages > 0:
        recent_messages = list(
            db.execute(
                select(Message)
                .where(Message.project_id == project.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(include_recent_messages)
            ).scalars()
        )

    # 2. Compose current reality
    reality_lines = [f"# Current Reality — {project.name}"]
    if project.description:
        reality_lines.append(project.description.strip())
    phase = state_json.get("phase")
    if phase:
        reality_lines.append(f"Phase: {phase}")
    objective = state_json.get("objective")
    if objective:
        reality_lines.append(f"Objective: {objective}")
    architecture = state_json.get("architecture")
    if architecture:
        reality_lines.append("Architecture:")
        if isinstance(architecture, dict):
            for k, v in architecture.items():
                reality_lines.append(f"- {k}: {v}")
        else:
            reality_lines.append(str(architecture))

    # 3. Changes
    changes_lines = ["# Changes"]
    if state is not None:
        changes_lines.append(f"- Current state version: {state.version}")
        if state.note:
            changes_lines.append(f"- Note: {state.note}")
    if decisions:
        changes_lines.append(f"- {len(decisions)} active decision(s) recorded.")
    if proposals:
        changes_lines.append(f"- {len(proposals)} open proposal(s) awaiting review.")
    if recent_messages:
        changes_lines.append(f"- {len(recent_messages)} recent conversation message(s) inspected.")

    # 4. Open questions
    open_questions: list[str] = []
    for p in problems:
        open_questions.append(f"Problem: {p.title}")
    for p in proposals:
        open_questions.append(f"Proposal pending decision: {p.title}")

    # 5. Role selection with strict caller precedence
    heuristic_role, heuristic_reason = _heuristic_pick_role(
        blocked=blocked,
        open_tasks=[t.title for t in open_tasks],
        problems=[p.title for p in problems],
    )

    if target_role:
        effective_role = target_role
        effective_reason = f"Caller explicitly designated target role: {target_role}."
    else:
        effective_role = heuristic_role
        effective_reason = heuristic_reason

    next_step = CombineNextStep(
        description=(
            f"Route next work to {effective_role}."
            + (f" Focus: {task}" if task else "")
        ),
        recommended_role=effective_role,
        reason=effective_reason,
    )

    # 6. Structured context prompt
    prompt_lines = [f"# Context for {effective_role}"]
    prompt_lines.append(f"Project: {project.name}")
    if task:
        prompt_lines.append(f"Task: {task}")
    prompt_lines.append("")
    prompt_lines.append("## Established decisions")
    if decisions:
        for d in decisions:
            prompt_lines.append(f"- {d.title}: {d.content}")
    else:
        prompt_lines.append("- (none recorded)")

    prompt_lines.append("")
    prompt_lines.append("## Active requirements")
    if requirements:
        for r in requirements:
            prompt_lines.append(f"- {r.title}: {r.content}")
    else:
        prompt_lines.append("- (none recorded)")

    prompt_lines.append("")
    prompt_lines.append("## Open tasks")
    if open_tasks:
        for t in open_tasks:
            prompt_lines.append(f"- {t.title}: {t.content}")
    else:
        prompt_lines.append("- (none)")

    prompt_lines.append("")
    prompt_lines.append("## Problems")
    if problems:
        for p in problems:
            prompt_lines.append(f"- {p.title}: {p.content}")
    else:
        prompt_lines.append("- (none)")

    if conflicts:
        prompt_lines.append("")
        prompt_lines.append("## Conflicts to resolve")
        for c in conflicts:
            prompt_lines.append(f"- [{c.kind}] {c.message}")

    if recent_messages:
        prompt_lines.append("")
        prompt_lines.append("## Recent Activity Excerpt")
        for m in reversed(recent_messages):
            snippet = m.content.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            prompt_lines.append(f"- [{m.role.value}] {snippet}")

    return CombineResult(
        project_id=project.id,
        generated_at=datetime.now(timezone.utc),
        current_reality_md="\n".join(reality_lines),
        changes_md="\n".join(changes_lines),
        completed=completed,
        active=active,
        blocked=blocked,
        conflicts=conflicts,
        open_questions=open_questions,
        recommended_next_step=next_step,
        suggested_next_ai_role=effective_role,
        suggested_next_ai_reason=effective_reason,
        generated_prompt_md="\n".join(prompt_lines),
    )
