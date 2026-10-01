"""Concrete background job handlers.

Registration happens at import time; the poller imports this module to
ensure handlers are available.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.orm import Session

from app.models.conversation import Capture, Conversation
from app.models.job import Job
from app.models.project import Project
from app.providers.groq_provider import build_default_provider
from app.services.extraction import build_conversation_text, extract_from_text
from app.services.jobs import register_handler

log = structlog.get_logger(__name__)


@register_handler("extract_memory")
def handle_extract_memory(db: Session, job: Job) -> None:
    payload = job.payload or {}
    capture_id_raw = payload.get("capture_id")
    conversation_id_raw = payload.get("conversation_id")

    if not capture_id_raw or not conversation_id_raw:
        raise ValueError("extract_memory job is missing required payload fields")

    if job.project_id is None:
        raise ValueError("extract_memory job requires an associated project_id")

    capture_id = uuid.UUID(str(capture_id_raw))
    conversation_id = uuid.UUID(str(conversation_id_raw))

    project = db.get(Project, job.project_id)
    conversation = db.get(Conversation, conversation_id)
    capture = db.get(Capture, capture_id)

    if project is None or conversation is None or capture is None:
        # Referenced entity was deleted; safe no-op
        log.info(
            "extract_memory_skipped_missing_entity",
            job_id=str(job.id),
            project_id=str(job.project_id),
        )
        return

    # Enforce strict project isolation across all referenced entities
    if conversation.project_id != project.id:
        raise ValueError(
            f"Cross-project entity mismatch: conversation {conversation.id} belongs to project {conversation.project_id}, not {project.id}"
        )
    if capture.project_id != project.id:
        raise ValueError(
            f"Cross-project entity mismatch: capture {capture.id} belongs to project {capture.project_id}, not {project.id}"
        )

    provider = build_default_provider()
    if provider is None:
        raise RuntimeError(
            "AI provider is not configured (GROQ_API_KEY missing)"
        )

    conversation_text = build_conversation_text(
        db, conversation_id=conversation.id, limit=60
    )
    if not conversation_text.strip():
        log.info(
            "extract_memory_skipped_empty_conversation",
            job_id=str(job.id),
            conversation_id=str(conversation_id),
        )
        return

    result = extract_from_text(
        db,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text=conversation_text,
        source_capture_id=capture.id,
        source_message_id=None,
    )

    log.info(
        "extract_memory_completed",
        job_id=str(job.id),
        project_id=str(project.id),
        capture_id=str(capture.id),
        items_inserted=result.inserted,
        conflicts=len(result.conflicts),
    )
