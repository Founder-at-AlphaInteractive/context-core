"""AI-backed extraction of structured memory items from conversations.

Invariants:
- Raw data preservation: failure during extraction NEVER destroys or modifies
  the raw captured conversation.
- Single atomic transaction: all extracted memory items and their corresponding
  events are committed together in ONE transaction. If an error occurs, the
  entire extracted batch is rolled back cleanly.
- Authority protection: AI extraction never manufactures authoritative project reality.
  All extracted items are created with AI_EXTRACTED or AI_PROPOSAL provenance in
  PROPOSED status.
- Strict schema validation: bounds checks, finite confidence [0.0, 1.0], no NaN/inf.
- Idempotent retry: ignores duplicate extractions for the same source_capture_id.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EventType, MemoryStatus, MemoryType, Provenance
from app.models.memory import MemoryItem
from app.providers.base import AIProvider, AIProviderError
from app.services.conflicts import detect_conflicts
from app.services.events import emit_event

EXTRACTION_SYSTEM_PROMPT = """You are a project memory extractor.
You read a conversation between a user and an AI working on a software project
and extract structured memory items.

Return STRICT JSON with this schema and nothing else:

{
  "items": [
    {
      "type": "fact | requirement | decision | proposal | task | problem",
      "title": "short title",
      "content": "concise description",
      "key": "optional stable key such as 'engine' or 'database'",
      "value": "optional value for the key",
      "confidence": 0.0,
      "provenance": "ai_extracted | ai_proposal"
    }
  ]
}

Rules:
- Use "decision" ONLY for things the user has clearly confirmed.
- Use "proposal" for AI-suggested changes that are not yet approved.
- Use "requirement" for stated constraints the project must satisfy.
- Use "fact" for established truths about the current project.
- Use "task" for discrete work items.
- Use "problem" for open issues or blockers.
- If unsure, use lower confidence.
- Do NOT include commentary outside the JSON.
"""

_EVENT_BY_TYPE = {
    MemoryType.DECISION: EventType.DECISION_CREATED,
    MemoryType.TASK: EventType.TASK_CREATED,
    MemoryType.PROBLEM: EventType.PROBLEM_CREATED,
}


@dataclass
class ExtractionResult:
    inserted: int
    conflicts: list[dict]


def _build_user_prompt(conversation_text: str, project_name: str) -> str:
    return (
        f"Project: {project_name}\n\n"
        f"Conversation:\n---\n{conversation_text}\n---\n"
        "Extract memory items now."
    )


def _validate_and_coerce_items(raw: dict) -> list[dict]:
    if not isinstance(raw, dict):
        raise AIProviderError("Extraction root response must be a JSON object")

    items = raw.get("items")
    if not isinstance(items, list):
        raise AIProviderError("Extraction 'items' must be a JSON array")

    validated: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        raw_type = it.get("type")
        if raw_type not in {m.value for m in MemoryType}:
            continue
        mem_type = MemoryType(raw_type)

        title = str(it.get("title") or "").strip()
        content = str(it.get("content") or "").strip()
        if not title or not content:
            continue

        title = title[:300]
        key = str(it["key"]).strip()[:200] if it.get("key") else None
        value = str(it["value"]).strip() if it.get("value") is not None else None

        # Validate confidence is a finite float in [0.0, 1.0]
        raw_conf = it.get("confidence", 0.5)
        try:
            conf = float(raw_conf)
            if math.isnan(conf) or math.isinf(conf):
                conf = 0.5
            else:
                conf = max(0.0, min(1.0, conf))
        except (ValueError, TypeError):
            conf = 0.5

        # Invariant: AI extraction CANNOT claim user-confirmed or active status
        raw_prov = str(it.get("provenance") or "").lower()
        if raw_prov == "ai_proposal" or mem_type == MemoryType.PROPOSAL:
            provenance = Provenance.AI_PROPOSAL
        else:
            provenance = Provenance.AI_EXTRACTED

        validated.append({
            "type": mem_type,
            "title": title,
            "content": content,
            "key": key,
            "value": value,
            "confidence": conf,
            "provenance": provenance,
        })

    return validated


def extract_from_text(
    db: Session,
    *,
    provider: AIProvider,
    project_id: uuid.UUID,
    project_name: str,
    conversation_text: str,
    source_capture_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
) -> ExtractionResult:
    prompt = _build_user_prompt(conversation_text, project_name)

    # 1. External AI provider call
    response_text = provider.complete_json(
        system=EXTRACTION_SYSTEM_PROMPT,
        user=prompt,
    )

    # 2. Strict JSON parsing with recovery attempt
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AIProviderError("Extraction response was not valid JSON")
        try:
            parsed = json.loads(response_text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIProviderError("Extraction JSON could not be parsed") from exc

    # 3. Validation and normalization
    validated_items = _validate_and_coerce_items(parsed)

    # 4. Idempotent check: find already extracted items for this capture
    existing_items: set[tuple[str, str, str | None]] = set()
    if source_capture_id is not None:
        rows = db.execute(
            select(MemoryItem.type, MemoryItem.title, MemoryItem.key).where(
                MemoryItem.project_id == project_id,
                MemoryItem.source_capture_id == source_capture_id,
            )
        ).all()
        for r in rows:
            existing_items.add((r[0].value, r[1], r[2]))

    # 5. Atomic single transaction for all items
    inserted = 0
    all_conflicts: list[dict] = []
    created_items: list[MemoryItem] = []

    try:
        for item_data in validated_items:
            identity_key = (item_data["type"].value, item_data["title"], item_data["key"])
            if identity_key in existing_items:
                continue

            # All extracted items strictly start in PROPOSED status
            item = MemoryItem(
                project_id=project_id,
                type=item_data["type"],
                status=MemoryStatus.PROPOSED,
                provenance=item_data["provenance"],
                title=item_data["title"],
                content=item_data["content"],
                key=item_data["key"],
                value=item_data["value"],
                confidence=item_data["confidence"],
                source_capture_id=source_capture_id,
                source_message_id=source_message_id,
            )
            db.add(item)
            db.flush()

            conflicts = detect_conflicts(db, project_id=project_id, candidate=item)
            for c in conflicts:
                all_conflicts.append(c.model_dump(mode="json"))

            event_type = _EVENT_BY_TYPE.get(item.type, EventType.MEMORY_CREATED)
            emit_event(
                db,
                project_id=project_id,
                event_type=event_type,
                entity_id=item.id,
                payload={
                    "memory_id": str(item.id),
                    "type": item.type.value,
                    "status": item.status.value,
                    "provenance": item.provenance.value,
                    "key": item.key,
                    "conflict_count": len(conflicts),
                },
            )
            created_items.append(item)
            existing_items.add(identity_key)
            inserted += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    return ExtractionResult(inserted=inserted, conflicts=all_conflicts)


def build_conversation_text(
    db: Session, *, conversation_id: uuid.UUID, limit: int = 60
) -> str:
    from app.models.conversation import Message

    messages = list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        ).scalars()
    )
    messages.reverse()
    lines: list[str] = []
    for m in messages:
        lines.append(f"[{m.role.value}] {m.content}")
    return "\n\n".join(lines)
