"""Conversation capture service.

Design:
- The authenticated Device.id is authoritative for client_id.
- A capture is idempotent on (client_id, local_id) backed by database unique constraint.
- Messages are deduplicated primarily by stable external_id per conversation,
  with content hashing as a fallback for stream overlap, preserving legitimate
  repeated messages.
- Conversation cursor represents stream synchronization position (including
  batches containing only duplicates).
- Atomicity: capture + messages + conversation cursor + event are committed together.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.conversation import Capture, Conversation, Message
from app.models.enums import EventType, MessageRole
from app.schemas.capture import CaptureMessageInput, CaptureRequest
from app.services.events import emit_event


@dataclass
class CaptureOutcome:
    capture: Capture
    conversation: Conversation
    messages_inserted: int
    messages_deduplicated: int
    idempotent_replay: bool


def _hash_content(role: MessageRole, content: str) -> str:
    payload = f"{role.value}\x1f{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _find_existing_capture(
    db: Session, *, client_id: uuid.UUID, local_id: uuid.UUID
) -> Capture | None:
    return db.execute(
        select(Capture).where(
            Capture.client_id == client_id,
            Capture.local_id == local_id,
        )
    ).scalar_one_or_none()


def _find_or_create_conversation(
    db: Session, *, project_id: uuid.UUID, payload: CaptureRequest
) -> Conversation:
    if payload.conversation_id is not None:
        conv = db.execute(
            select(Conversation)
            .where(Conversation.id == payload.conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        if conv is None or conv.project_id != project_id:
            raise ValueError("Conversation not found in this project")
        return conv

    if payload.external_thread_id:
        conv = db.execute(
            select(Conversation)
            .where(
                Conversation.project_id == project_id,
                Conversation.external_thread_id == payload.external_thread_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if conv is not None:
            return conv

    conv = Conversation(
        project_id=project_id,
        ai_profile_id=payload.ai_profile_id,
        project_ai_role_id=payload.project_ai_role_id,
        title=payload.title,
        provider=payload.provider,
        external_thread_id=payload.external_thread_id,
        cursor={},
    )
    db.add(conv)
    db.flush()
    return conv


def _insert_messages(
    db: Session,
    *,
    project_id: uuid.UUID,
    conversation: Conversation,
    incoming: list[CaptureMessageInput],
) -> tuple[int, int, dict | None]:
    inserted = 0
    deduped = 0
    last_cursor: dict | None = None

    if incoming:
        # Cursor represents stream synchronization position reached by this batch
        last_msg = incoming[-1]
        last_hash = last_msg.content_hash or _hash_content(last_msg.role, last_msg.content)
        last_cursor = {
            "last_external_id": last_msg.external_id,
            "last_hash": last_hash,
        }

    # Preload existing external_ids for this conversation
    existing_external_ids = set(
        db.execute(
            select(Message.external_id).where(
                Message.conversation_id == conversation.id,
                Message.external_id.isnot(None),
            )
        ).scalars()
    )

    seen_external_ids = set(existing_external_ids)
    cursor_last_hash = (conversation.cursor or {}).get("last_hash")
    cursor_last_ext_id = (conversation.cursor or {}).get("last_external_id")

    for i, msg in enumerate(incoming):
        content_hash = msg.content_hash or _hash_content(msg.role, msg.content)

        # 1. Primary deduplication: stable external_id
        if msg.external_id is not None:
            if msg.external_id in seen_external_ids:
                deduped += 1
                continue
            seen_external_ids.add(msg.external_id)
        else:
            # 2. Fallback deduplication: match cursor last_hash on initial message of overlap batch
            if (
                i == 0
                and cursor_last_ext_id is None
                and cursor_last_hash is not None
                and content_hash == cursor_last_hash
            ):
                deduped += 1
                continue

        # Preserve legitimate repeated messages when not duplicates by external_id or cursor overlap
        message = Message(
            project_id=project_id,
            conversation_id=conversation.id,
            role=msg.role,
            content=msg.content,
            external_id=msg.external_id,
            content_hash=content_hash,
            captured_at=msg.captured_at,
        )
        db.add(message)
        inserted += 1

    return inserted, deduped, last_cursor


def capture_conversation(
    db: Session,
    *,
    project_id: uuid.UUID,
    client_id: uuid.UUID,
    payload: CaptureRequest,
) -> CaptureOutcome:
    # 1. Check for existing capture replay using authoritative client_id and local_id
    existing = _find_existing_capture(
        db, client_id=client_id, local_id=payload.local_id
    )
    if existing is not None:
        conv = db.get(Conversation, existing.conversation_id)
        return CaptureOutcome(
            capture=existing,
            conversation=conv,
            messages_inserted=0,
            messages_deduplicated=existing.message_count,
            idempotent_replay=True,
        )

    # 2. Serialize conversation mutation under row lock
    conversation = _find_or_create_conversation(
        db, project_id=project_id, payload=payload
    )

    inserted, deduped, last_cursor = _insert_messages(
        db,
        project_id=project_id,
        conversation=conversation,
        incoming=payload.messages,
    )

    if last_cursor is not None:
        merged_cursor = dict(conversation.cursor or {})
        merged_cursor.update(last_cursor)
        merged_cursor["mode"] = payload.mode.value
        conversation.cursor = merged_cursor

    capture = Capture(
        project_id=project_id,
        conversation_id=conversation.id,
        client_id=client_id,
        local_id=payload.local_id,
        mode=payload.mode,
        payload=payload.model_dump(mode="json"),
        message_count=inserted,
        status="stored",
    )

    # 3. Handle concurrent duplicate insertion race via savepoint
    try:
        with db.begin_nested():
            db.add(capture)
            db.flush()
    except IntegrityError:
        existing = _find_existing_capture(
            db, client_id=client_id, local_id=payload.local_id
        )
        if existing is not None:
            conv = db.get(Conversation, existing.conversation_id)
            return CaptureOutcome(
                capture=existing,
                conversation=conv,
                messages_inserted=0,
                messages_deduplicated=existing.message_count,
                idempotent_replay=True,
            )
        raise

    # 4. Atomically emit event with minimal payload in the same transaction
    emit_event(
        db,
        project_id=project_id,
        event_type=EventType.CONVERSATION_CAPTURED,
        entity_id=capture.id,
        payload={
            "conversation_id": str(conversation.id),
            "capture_id": str(capture.id),
            "mode": payload.mode.value,
            "messages_inserted": inserted,
            "messages_deduplicated": deduped,
        },
        client_id=client_id,
    )

    db.commit()
    db.refresh(capture)
    db.refresh(conversation)

    return CaptureOutcome(
        capture=capture,
        conversation=conversation,
        messages_inserted=inserted,
        messages_deduplicated=deduped,
        idempotent_replay=False,
    )


def list_conversations(
    db: Session, project_id: uuid.UUID, limit: int = 100
) -> list[Conversation]:
    return list(
        db.execute(
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
        ).scalars()
    )


def get_conversation(
    db: Session, project_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.project_id != project_id:
        return None
    return conv


def list_messages(
    db: Session, project_id: uuid.UUID, conversation_id: uuid.UUID, limit: int = 500
) -> list[Message]:
    return list(
        db.execute(
            select(Message)
            .where(
                Message.project_id == project_id,
                Message.conversation_id == conversation_id,
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        ).scalars()
    )
