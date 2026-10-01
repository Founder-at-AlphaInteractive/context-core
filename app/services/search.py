"""Search service.

Provides deterministic project-scoped search across structured memory,
messages, conversations, and handoffs using PostgreSQL ILIKE substring matching.

Invariants:
- Project isolation: every query strictly filters by project_id.
- Query validation: rejects empty or whitespace-only queries.
- Bounded results: enforces deterministic limit bounds (1 to 200).
- Deterministic ordering: stable timestamps with primary key tie-breakers.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message
from app.models.handoff import Handoff
from app.models.memory import MemoryItem


def search_project(
    db: Session,
    *,
    project_id: uuid.UUID,
    query: str,
    limit: int = 50,
) -> dict:
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("Search query cannot be empty or whitespace only")

    effective_limit = max(1, min(200, limit))
    pattern = f"%{clean_query}%"

    memory_hits = list(
        db.execute(
            select(MemoryItem)
            .where(
                MemoryItem.project_id == project_id,
                or_(
                    MemoryItem.title.ilike(pattern),
                    MemoryItem.content.ilike(pattern),
                    MemoryItem.key.ilike(pattern),
                    MemoryItem.value.ilike(pattern),
                ),
            )
            .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
            .limit(effective_limit)
        ).scalars()
    )

    message_hits = list(
        db.execute(
            select(Message)
            .where(
                Message.project_id == project_id,
                Message.content.ilike(pattern),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(effective_limit)
        ).scalars()
    )

    conversation_hits = list(
        db.execute(
            select(Conversation)
            .where(
                Conversation.project_id == project_id,
                Conversation.title.ilike(pattern),
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(effective_limit)
        ).scalars()
    )

    handoff_hits = list(
        db.execute(
            select(Handoff)
            .where(
                Handoff.project_id == project_id,
                or_(
                    Handoff.topic.ilike(pattern),
                    Handoff.content_md.ilike(pattern),
                ),
            )
            .order_by(Handoff.created_at.desc(), Handoff.id.desc())
            .limit(effective_limit)
        ).scalars()
    )

    return {
        "memory": memory_hits,
        "messages": message_hits,
        "conversations": conversation_hits,
        "handoffs": handoff_hits,
    }
