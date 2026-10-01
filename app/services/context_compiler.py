"""Context Compiler.

Given a project, a target role, and a task, produce a minimal Markdown
context that respects a hard token budget using deterministic item-level packing.

Core invariants:
1. Hard token budget: the generated content strictly obeys the token budget
   calculated via a conservative ceiling estimator.
2. Authority visibility: active authoritative decisions and unapproved AI proposals
   are explicitly marked with their provenance and status.
3. Item-level greedy packing: items are scored, sorted deterministically, and packed
   individually rather than dumping entire monolithic sections.
4. Exact memory tracking: included_memory_ids accurately records exactly the
   memory items packed into the output.
5. Tolerant state rendering: safely handles malformed or non-list state JSON.
6. Pure compilation: does not mutate project state or database entities.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.conversation import Message
from app.models.enums import (
    PROVENANCE_RANK,
    MemoryStatus,
    MemoryType,
    Provenance,
)
from app.models.handoff import ContextSnapshot
from app.models.memory import MemoryItem
from app.models.project import Project, ProjectState
from app.services import state as state_service

CHARS_PER_TOKEN = 4

ROLE_TEMPLATES: dict[str, dict] = {
    "architecture": {
        "types": [
            MemoryType.DECISION,
            MemoryType.REQUIREMENT,
            MemoryType.FACT,
            MemoryType.PROBLEM,
            MemoryType.PROPOSAL,
        ],
        "section_priority": [
            "identity",
            "objective",
            "architecture",
            "current_state",
            "decisions",
            "requirements",
            "problems",
            "proposals",
            "facts",
            "recent",
        ],
    },
    "technical_advisor": {
        "types": [
            MemoryType.DECISION,
            MemoryType.FACT,
            MemoryType.PROBLEM,
            MemoryType.PROPOSAL,
        ],
        "section_priority": [
            "identity",
            "architecture",
            "current_state",
            "problems",
            "decisions",
            "facts",
            "proposals",
            "recent",
        ],
    },
    "coding_architect": {
        "types": [
            MemoryType.DECISION,
            MemoryType.REQUIREMENT,
            MemoryType.TASK,
            MemoryType.FACT,
            MemoryType.PROBLEM,
        ],
        "section_priority": [
            "identity",
            "task",
            "current_state",
            "architecture",
            "requirements",
            "decisions",
            "tasks",
            "problems",
            "facts",
            "recent",
        ],
    },
    "implementation_agent": {
        "types": [
            MemoryType.TASK,
            MemoryType.REQUIREMENT,
            MemoryType.DECISION,
            MemoryType.FACT,
        ],
        "section_priority": [
            "identity",
            "task",
            "current_state",
            "tasks",
            "requirements",
            "decisions",
            "facts",
            "recent",
        ],
    },
}

DEFAULT_TYPE_WEIGHTS = {
    MemoryType.DECISION: 3.5,
    MemoryType.REQUIREMENT: 3.0,
    MemoryType.PROBLEM: 2.5,
    MemoryType.TASK: 2.0,
    MemoryType.FACT: 1.5,
    MemoryType.PROPOSAL: 0.5,
}


def estimate_tokens(text: str) -> int:
    """Conservative ceiling token estimator with delimiter overhead."""
    if not text:
        return 0
    # Add 1 token formatting/newline overhead per block
    return math.ceil(len(text) / CHARS_PER_TOKEN) + 1


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _role_template(role: str) -> dict:
    key = _slugify(role)
    if key in ROLE_TEMPLATES:
        return ROLE_TEMPLATES[key]
    # Heuristics for custom role names
    if "arch" in key and "code" not in key:
        return ROLE_TEMPLATES["architecture"]
    if "advisor" in key or "review" in key:
        return ROLE_TEMPLATES["technical_advisor"]
    if "code" in key or "coder" in key or "planner" in key:
        return ROLE_TEMPLATES["coding_architect"]
    if "implement" in key or "agent" in key or "worker" in key:
        return ROLE_TEMPLATES["implementation_agent"]
    return ROLE_TEMPLATES["technical_advisor"]


def _task_tokens(task: str | None) -> set[str]:
    if not task:
        return set()
    return {t for t in re.findall(r"[a-z0-9_]+", task.lower()) if len(t) >= 3}


def _score_memory(
    item: MemoryItem,
    *,
    task_tokens: set[str],
) -> float:
    base = DEFAULT_TYPE_WEIGHTS.get(item.type, 1.0)
    # Provenance weighting: user-confirmed outranks proposals
    prov_rank = PROVENANCE_RANK.get(item.provenance, 0)
    base += (prov_rank / 100.0) * 3.0

    if item.status == MemoryStatus.ACTIVE:
        base += 2.0
    elif item.status == MemoryStatus.PROPOSED:
        base += 0.5
    elif item.status in (MemoryStatus.SUPERSEDED, MemoryStatus.REJECTED):
        base -= 10.0
    elif item.status == MemoryStatus.COMPLETED:
        base -= 1.0

    if task_tokens:
        haystack = f"{item.title}\n{item.content}\n{item.key or ''}\n{item.value or ''}".lower()
        for tok in task_tokens:
            if tok in haystack:
                base += 1.0

    base += max(0.0, min(1.0, item.confidence)) * 0.5
    return base


def _render_memory_item(item: MemoryItem) -> str:
    """Render a memory item with clear authority/provenance markers."""
    if item.status == MemoryStatus.PROPOSED:
        status_tag = f"[{item.provenance.value.upper()} | PROPOSED (Unapproved)]"
    elif item.status == MemoryStatus.ACTIVE:
        status_tag = f"[{item.provenance.value.upper()} | ACTIVE]"
    else:
        status_tag = f"[{item.provenance.value.upper()} | {item.status.value.upper()}]"

    head = f"- {status_tag} **{item.title}**"
    if item.key:
        if item.value:
            head += f" (`{item.key} = {item.value}`)"
        else:
            head += f" (`{item.key}`)"
    body = item.content.strip().replace("\r\n", "\n")
    return f"{head}\n  {body}"


@dataclass
class CompiledContext:
    content_md: str
    token_count: int
    sections: dict[str, str]
    included_memory_ids: list[uuid.UUID]
    state_version: int | None


def _load_state(db: Session, project_id: uuid.UUID) -> ProjectState | None:
    return state_service.get_current_state(db, project_id)


def _load_memory_candidates(
    db: Session, project_id: uuid.UUID
) -> list[MemoryItem]:
    return list(
        db.execute(
            select(MemoryItem)
            .where(
                MemoryItem.project_id == project_id,
                MemoryItem.status.in_(
                    [
                        MemoryStatus.ACTIVE,
                        MemoryStatus.PROPOSED,
                        MemoryStatus.COMPLETED,
                    ]
                ),
            )
            .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
            .limit(500)
        ).scalars()
    )


def _load_recent_messages(
    db: Session,
    project_id: uuid.UUID,
    limit: int,
    conversation_id: uuid.UUID | None = None,
) -> list[Message]:
    if limit <= 0:
        return []
    stmt = select(Message).where(Message.project_id == project_id)
    if conversation_id is not None:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def _safe_format_list(items) -> list[str]:
    """Safely format items that may not be a list in unexpected state JSON."""
    if isinstance(items, list):
        return [str(x) for x in items]
    if isinstance(items, (str, int, float, bool)):
        return [str(items)]
    if isinstance(items, dict):
        return [f"{k}: {v}" for k, v in items.items()]
    return []


def compile_context(
    db: Session,
    *,
    project: Project,
    role: str,
    task: str | None,
    token_budget: int | None,
    include_recent_messages: int = 20,
    conversation_id: uuid.UUID | None = None,
) -> CompiledContext:
    # 1. Resolve and validate hard token budget
    if token_budget is None:
        budget = settings.context_default_token_budget
    else:
        if token_budget <= 0:
            raise ValueError("token_budget must be greater than zero")
        if token_budget < 50 or token_budget > 100_000:
            raise ValueError("token_budget must be between 50 and 100000")
        budget = token_budget

    template = _role_template(role)
    task_tokens = _task_tokens(task)

    state = _load_state(db, project.id)
    candidates = _load_memory_candidates(db, project.id)
    recent = _load_recent_messages(
        db, project.id, include_recent_messages, conversation_id=conversation_id
    )

    allowed_types = set(template["types"])
    filtered = [c for c in candidates if c.type in allowed_types]

    # 2. Sort deterministically using: score, provenance, status, recency, stable UUID tie-breaker
    def _sort_key(item: MemoryItem):
        score = _score_memory(item, task_tokens=task_tokens)
        prov_rank = PROVENANCE_RANK.get(item.provenance, 0)
        status_rank = 1 if item.status == MemoryStatus.ACTIVE else 0
        recency = item.updated_at.timestamp() if item.updated_at else 0.0
        return (score, prov_rank, status_rank, recency, str(item.id))

    sorted_candidates = sorted(filtered, key=_sort_key, reverse=True)

    # 3. Prepare static structural sections
    state_json = dict(state.state_json) if state is not None else {}
    sections_rendered: dict[str, str] = {}

    # Identity
    identity_lines = [f"# Project: {project.name}"]
    if project.description:
        identity_lines.append(project.description.strip())
    sections_rendered["identity"] = "\n".join(identity_lines).strip()

    # Objective
    objective = state_json.get("objective")
    if objective:
        if isinstance(objective, dict):
            obj_text = objective.get("description") or str(objective)
        else:
            obj_text = str(objective)
        sections_rendered["objective"] = f"## Objective\n{obj_text}"

    # Architecture
    architecture = state_json.get("architecture")
    if architecture:
        lines = ["## Architecture"]
        if isinstance(architecture, dict):
            for k, v in architecture.items():
                lines.append(f"- {k}: {v}")
        else:
            lines.append(str(architecture))
        sections_rendered["architecture"] = "\n".join(lines)

    # Current State (safe formatting)
    phase = state_json.get("phase")
    completed = _safe_format_list(state_json.get("completed"))
    active = _safe_format_list(state_json.get("active"))
    blocked = _safe_format_list(state_json.get("blocked"))
    next_steps = _safe_format_list(state_json.get("next"))

    state_lines = ["## Current State"]
    if phase:
        state_lines.append(f"- Phase: {phase}")
    if completed:
        state_lines.append("- Completed:")
        for c in completed:
            state_lines.append(f"  - {c}")
    if active:
        state_lines.append("- Active:")
        for a in active:
            state_lines.append(f"  - {a}")
    if blocked:
        state_lines.append("- Blocked:")
        for b in blocked:
            state_lines.append(f"  - {b}")
    if next_steps:
        state_lines.append("- Next:")
        for n in next_steps:
            state_lines.append(f"  - {n}")
    sections_rendered["current_state"] = "\n".join(state_lines)

    # Current Task
    if task:
        sections_rendered["task"] = f"## Current Task\n{task}"

    # Group candidate items by section
    type_to_section = {
        MemoryType.DECISION: ("decisions", "Active Decisions"),
        MemoryType.REQUIREMENT: ("requirements", "Requirements"),
        MemoryType.PROBLEM: ("problems", "Known Problems"),
        MemoryType.TASK: ("tasks", "Open Tasks"),
        MemoryType.FACT: ("facts", "Established Facts"),
        MemoryType.PROPOSAL: ("proposals", "Open Proposals (not yet accepted)"),
    }

    items_by_section: dict[str, list[MemoryItem]] = {
        sec: [] for sec, _ in type_to_section.values()
    }
    for item in sorted_candidates:
        sec_name = type_to_section.get(item.type, (None, None))[0]
        if sec_name:
            items_by_section[sec_name].append(item)

    # 4. Item-level greedy packing obeying hard token budget
    included_memory_ids: list[uuid.UUID] = []
    packed_blocks: list[str] = []
    used_tokens = 0

    def _can_fit(text: str) -> bool:
        return used_tokens + estimate_tokens(text) <= budget

    def _pack(text: str) -> bool:
        nonlocal used_tokens
        cost = estimate_tokens(text)
        if used_tokens + cost <= budget:
            packed_blocks.append(text)
            used_tokens += cost
            return True
        return False

    # Walk sections in role priority order
    priority = template.get("section_priority", [
        "identity", "objective", "architecture", "current_state", "task",
        "decisions", "requirements", "problems", "tasks", "facts", "proposals", "recent"
    ])

    for sec in priority:
        if sec in ("identity", "objective", "architecture", "current_state", "task"):
            text = sections_rendered.get(sec)
            if text and _can_fit(text):
                _pack(text)
        elif sec in items_by_section:
            # Memory-driven section: pack individually item-by-item
            section_items = items_by_section[sec]
            if not section_items:
                continue

            sec_title = next(t for s, t in type_to_section.values() if s == sec)
            header_text = f"## {sec_title}"
            header_packed = False

            for item in section_items:
                item_text = _render_memory_item(item)
                required_tokens = estimate_tokens(item_text) + (
                    estimate_tokens(header_text) if not header_packed else 0
                )
                if used_tokens + required_tokens <= budget:
                    if not header_packed:
                        _pack(header_text)
                        header_packed = True
                    _pack(item_text)
                    included_memory_ids.append(item.id)
        elif sec == "recent" and recent:
            # Optional recent messages: pack message-by-message while fitting
            header = "## Recent Conversation Excerpt"
            header_packed = False
            for msg in reversed(recent):
                snippet = msg.content.strip().replace("\n", " ")
                if len(snippet) > 300:
                    snippet = snippet[:300] + "…"
                msg_line = f"- [{msg.role.value}] {snippet}"
                req_cost = estimate_tokens(msg_line) + (
                    estimate_tokens(header) if not header_packed else 0
                )
                if used_tokens + req_cost <= budget:
                    if not header_packed:
                        _pack(header)
                        header_packed = True
                    _pack(msg_line)

    content_md = "\n\n".join(packed_blocks).strip()
    final_token_count = estimate_tokens(content_md)

    # 5. Final budget invariant assertion
    if final_token_count > budget and packed_blocks:
        # Emergency safety trim if markdown joining added edge tokens
        while packed_blocks and estimate_tokens("\n\n".join(packed_blocks).strip()) > budget:
            popped = packed_blocks.pop()
            # If popped was an item, remove its id if tracked
            for item in sorted_candidates:
                if item.id in included_memory_ids and item.title in popped:
                    included_memory_ids.remove(item.id)
                    break
        content_md = "\n\n".join(packed_blocks).strip()
        final_token_count = estimate_tokens(content_md)

    assert final_token_count <= budget, f"Compiled tokens ({final_token_count}) exceeded budget ({budget})"

    return CompiledContext(
        content_md=content_md,
        token_count=final_token_count,
        sections=sections_rendered,
        included_memory_ids=included_memory_ids,
        state_version=state.version if state is not None else None,
    )


def persist_snapshot(
    db: Session,
    *,
    project_id: uuid.UUID,
    role: str,
    task: str | None,
    compiled: CompiledContext,
    commit: bool = True,
) -> ContextSnapshot:
    """Persist context snapshot.

    If commit=False, performs db.flush() only, allowing atomic inclusion
    in larger compound transactions (e.g. handoff creation).
    """
    snapshot = ContextSnapshot(
        project_id=project_id,
        role=role,
        task=task,
        content_md=compiled.content_md,
        token_count=compiled.token_count,
        project_state_version=compiled.state_version,
    )
    db.add(snapshot)
    if commit:
        db.commit()
        db.refresh(snapshot)
    else:
        db.flush()
    return snapshot
