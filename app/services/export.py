"""Project export service.

Produces a portable, versioned JSON document containing all project-scoped
data for backup, inspection, or migration.

Invariants:
- Consistency: executes within a single read transaction to ensure an internally
  consistent snapshot of project state.
- Security: NEVER exports device secrets, bearer tokens, or credentials.
- Sequence preservation: events are strictly ordered by ascending sequence without renumbering.
- Explicit schema_version = 1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_profile import AIProfile, ProjectAIRole
from app.models.conversation import Capture, Conversation, Message
from app.models.event import Event
from app.models.handoff import ContextSnapshot, Handoff
from app.models.memory import MemoryItem
from app.models.project import Project, ProjectState


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def export_project(db: Session, project: Project) -> dict:
    """Exports a coherent project snapshot."""
    # Ensure fresh read of project metadata
    db.refresh(project)

    states = list(
        db.execute(
            select(ProjectState)
            .where(ProjectState.project_id == project.id)
            .order_by(ProjectState.version.asc())
        ).scalars()
    )
    roles = list(
        db.execute(
            select(ProjectAIRole)
            .where(ProjectAIRole.project_id == project.id)
            .order_by(ProjectAIRole.created_at.asc())
        ).scalars()
    )
    ai_profile_ids = {r.ai_profile_id for r in roles}
    profiles = (
        list(
            db.execute(
                select(AIProfile)
                .where(AIProfile.id.in_(ai_profile_ids))
                .order_by(AIProfile.created_at.asc())
            ).scalars()
        )
        if ai_profile_ids
        else []
    )

    conversations = list(
        db.execute(
            select(Conversation)
            .where(Conversation.project_id == project.id)
            .order_by(Conversation.created_at.asc())
        ).scalars()
    )
    messages = list(
        db.execute(
            select(Message)
            .where(Message.project_id == project.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).scalars()
    )
    captures = list(
        db.execute(
            select(Capture)
            .where(Capture.project_id == project.id)
            .order_by(Capture.created_at.asc())
        ).scalars()
    )
    memory_items = list(
        db.execute(
            select(MemoryItem)
            .where(MemoryItem.project_id == project.id)
            .order_by(MemoryItem.created_at.asc(), MemoryItem.id.asc())
        ).scalars()
    )
    handoffs = list(
        db.execute(
            select(Handoff)
            .where(Handoff.project_id == project.id)
            .order_by(Handoff.created_at.asc())
        ).scalars()
    )
    snapshots = list(
        db.execute(
            select(ContextSnapshot)
            .where(ContextSnapshot.project_id == project.id)
            .order_by(ContextSnapshot.created_at.asc())
        ).scalars()
    )
    events = list(
        db.execute(
            select(Event)
            .where(Event.project_id == project.id)
            .order_by(Event.sequence.asc())
        ).scalars()
    )

    return {
        "schema_version": 1,
        "exported_at": _iso(datetime.now(timezone.utc)),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "last_event_sequence": project.last_event_sequence,
            "created_at": _iso(project.created_at),
            "updated_at": _iso(project.updated_at),
        },
        "ai_profiles": [
            {
                "id": str(p.id),
                "name": p.name,
                "provider": p.provider,
                "type": p.type.value,
                "detection_hints": p.detection_hints,
                "permissions": p.permissions,
                "created_at": _iso(p.created_at),
                "updated_at": _iso(p.updated_at),
            }
            for p in profiles
        ],
        "project_ai_roles": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "ai_profile_id": str(r.ai_profile_id),
                "role_name": r.role_name,
                "permissions": r.permissions,
                "created_at": _iso(r.created_at),
            }
            for r in roles
        ],
        "conversations": [
            {
                "id": str(c.id),
                "project_id": str(c.project_id),
                "ai_profile_id": str(c.ai_profile_id) if c.ai_profile_id else None,
                "project_ai_role_id": (
                    str(c.project_ai_role_id) if c.project_ai_role_id else None
                ),
                "title": c.title,
                "provider": c.provider,
                "external_thread_id": c.external_thread_id,
                "cursor": c.cursor,
                "created_at": _iso(c.created_at),
                "updated_at": _iso(c.updated_at),
            }
            for c in conversations
        ],
        "messages": [
            {
                "id": str(m.id),
                "project_id": str(m.project_id),
                "conversation_id": str(m.conversation_id),
                "role": m.role.value,
                "content": m.content,
                "external_id": m.external_id,
                "content_hash": m.content_hash,
                "captured_at": _iso(m.captured_at),
                "created_at": _iso(m.created_at),
            }
            for m in messages
        ],
        "captures": [
            {
                "id": str(c.id),
                "project_id": str(c.project_id),
                "conversation_id": str(c.conversation_id),
                "client_id": str(c.client_id),
                "local_id": str(c.local_id),
                "mode": c.mode.value,
                "message_count": c.message_count,
                "status": c.status,
                "created_at": _iso(c.created_at),
            }
            for c in captures
        ],
        "memory_items": [
            {
                "id": str(m.id),
                "project_id": str(m.project_id),
                "type": m.type.value,
                "status": m.status.value,
                "provenance": m.provenance.value,
                "title": m.title,
                "content": m.content,
                "key": m.key,
                "value": m.value,
                "confidence": m.confidence,
                "tags": m.tags,
                "source_capture_id": (
                    str(m.source_capture_id) if m.source_capture_id else None
                ),
                "source_message_id": (
                    str(m.source_message_id) if m.source_message_id else None
                ),
                "supersedes_id": (
                    str(m.supersedes_id) if m.supersedes_id else None
                ),
                "created_at": _iso(m.created_at),
                "updated_at": _iso(m.updated_at),
            }
            for m in memory_items
        ],
        "project_states": [
            {
                "id": str(s.id),
                "project_id": str(s.project_id),
                "version": s.version,
                "state_json": s.state_json,
                "created_by_device_id": (
                    str(s.created_by_device_id)
                    if s.created_by_device_id
                    else None
                ),
                "note": s.note,
                "created_at": _iso(s.created_at),
            }
            for s in states
        ],
        "handoffs": [
            {
                "id": str(h.id),
                "project_id": str(h.project_id),
                "from_ai_profile_id": (
                    str(h.from_ai_profile_id) if h.from_ai_profile_id else None
                ),
                "from_role": h.from_role,
                "to_ai_profile_id": (
                    str(h.to_ai_profile_id) if h.to_ai_profile_id else None
                ),
                "to_role": h.to_role,
                "topic": h.topic,
                "content_md": h.content_md,
                "context_snapshot_id": (
                    str(h.context_snapshot_id) if h.context_snapshot_id else None
                ),
                "created_at": _iso(h.created_at),
            }
            for h in handoffs
        ],
        "context_snapshots": [
            {
                "id": str(s.id),
                "project_id": str(s.project_id),
                "role": s.role,
                "task": s.task,
                "content_md": s.content_md,
                "token_count": s.token_count,
                "project_state_version": s.project_state_version,
                "created_at": _iso(s.created_at),
            }
            for s in snapshots
        ],
        "events": [
            {
                "id": str(e.id),
                "project_id": str(e.project_id),
                "sequence": e.sequence,
                "type": e.type.value,
                "entity_id": str(e.entity_id) if e.entity_id else None,
                "payload": e.payload,
                "client_id": str(e.client_id) if e.client_id else None,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ],
    }
