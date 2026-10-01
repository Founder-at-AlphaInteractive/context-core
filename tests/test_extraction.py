import json
import uuid
import pytest
from sqlalchemy import select

from app.models.conversation import Capture, Conversation
from app.models.enums import CaptureMode, MemoryStatus, MemoryType, Provenance
from app.models.memory import MemoryItem
from app.models.project import Project
from app.providers.base import AIProvider, AIProviderError
from app.services import extraction as extraction_service


class DummyAIProvider(AIProvider):
    def __init__(self, response_text: str | None = None, raise_error: Exception | None = None):
        self.response_text = response_text
        self.raise_error = raise_error

    def complete_json(self, *, system: str, user: str) -> str:
        if self.raise_error:
            raise self.raise_error
        return self.response_text or "{}"

    def is_configured(self) -> bool:
        return True


@pytest.fixture
def extraction_setup(db_session):
    project = Project(id=uuid.uuid4(), name="Extraction Project", last_event_sequence=0)
    db_session.add(project)
    db_session.flush()

    conv = Conversation(project_id=project.id, cursor={})
    db_session.add(conv)
    db_session.flush()

    capture = Capture(
        project_id=project.id,
        conversation_id=conv.id,
        client_id=uuid.uuid4(),
        local_id=uuid.uuid4(),
        mode=CaptureMode.LAST_EXCHANGE,
        payload={"dummy": True},
        message_count=2,
        status="stored",
    )
    db_session.add(capture)
    db_session.commit()

    db_session.refresh(project)
    db_session.refresh(conv)
    db_session.refresh(capture)
    return project, conv, capture


def test_extraction_valid_json_and_ai_provenance(db_session, extraction_setup):
    project, _, capture = extraction_setup

    provider_json = json.dumps({
        "items": [
            {
                "type": "decision",
                "title": "Database is Postgres",
                "content": "User agreed to Postgres.",
                "key": "db",
                "value": "PostgreSQL",
                "confidence": 0.95,
                "provenance": "ai_extracted",
            },
            {
                "type": "proposal",
                "title": "Use Redis for Cache",
                "content": "AI suggests Redis caching.",
                "key": "cache",
                "value": "Redis",
                "confidence": 0.7,
                "provenance": "ai_proposal",
            },
        ]
    })

    provider = DummyAIProvider(response_text=provider_json)
    res = extraction_service.extract_from_text(
        db_session,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text="User: Let's use postgres. AI: Sure, what about redis?",
        source_capture_id=capture.id,
    )

    assert res.inserted == 2
    items = db_session.execute(
        select(MemoryItem).where(MemoryItem.project_id == project.id).order_by(MemoryItem.created_at.asc())
    ).scalars().all()
    assert len(items) == 2

    # Invariant: AI extraction MUST NOT manufacture active authority
    assert items[0].status == MemoryStatus.PROPOSED
    assert items[0].provenance in (Provenance.AI_EXTRACTED, Provenance.AI_PROPOSAL)
    assert items[1].status == MemoryStatus.PROPOSED
    assert items[1].provenance == Provenance.AI_PROPOSAL


def test_extraction_recovery_parsing(db_session, extraction_setup):
    project, _, capture = extraction_setup

    # Text containing commentary surrounding the JSON
    messy_response = (
        "Here is the extracted information you requested:\n"
        "```json\n"
        '{"items": [{"type": "fact", "title": "Version 1.0", "content": "Currently v1.0", "confidence": 0.9}]}\n'
        "```\nHope this helps!"
    )
    provider = DummyAIProvider(response_text=messy_response)
    res = extraction_service.extract_from_text(
        db_session,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text="Conversation text",
        source_capture_id=capture.id,
    )
    assert res.inserted == 1
    item = db_session.execute(
        select(MemoryItem).where(MemoryItem.title == "Version 1.0")
    ).scalar_one_or_none()
    assert item is not None
    assert item.content == "Currently v1.0"


def test_extraction_malformed_json_failure_leaves_raw_capture_intact(db_session, extraction_setup):
    project, _, capture = extraction_setup

    provider = DummyAIProvider(response_text="Completely unparseable garbage without braces")
    with pytest.raises(AIProviderError, match="not valid JSON"):
        extraction_service.extract_from_text(
            db_session,
            provider=provider,
            project_id=project.id,
            project_name=project.name,
            conversation_text="Conversation text",
            source_capture_id=capture.id,
        )

    # Invariant: Raw capture remains intact
    db_session.rollback()
    refreshed_capture = db_session.get(Capture, capture.id)
    assert refreshed_capture is not None
    assert refreshed_capture.status == "stored"

    # Zero memory items inserted
    items = db_session.execute(
        select(MemoryItem).where(MemoryItem.project_id == project.id)
    ).scalars().all()
    assert len(items) == 0


def test_extraction_invalid_confidence_and_bounds_normalized(db_session, extraction_setup):
    project, _, capture = extraction_setup

    # Confidence out of bounds (1.5) and negative (-0.2)
    provider_json = json.dumps({
        "items": [
            {
                "type": "fact",
                "title": "A" * 400,  # Exceeds 300
                "content": "Valid content",
                "confidence": 1.5,
            },
            {
                "type": "task",
                "title": "Valid task",
                "content": "Task content",
                "confidence": -0.2,
            },
        ]
    })
    provider = DummyAIProvider(response_text=provider_json)
    res = extraction_service.extract_from_text(
        db_session,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text="Conversation text",
        source_capture_id=capture.id,
    )
    assert res.inserted == 2
    items = db_session.execute(
        select(MemoryItem).where(MemoryItem.project_id == project.id).order_by(MemoryItem.id.asc())
    ).scalars().all()

    # Title clamped to 300 chars, confidence normalized to [0.0, 1.0]
    for it in items:
        assert len(it.title) <= 300
        assert 0.0 <= it.confidence <= 1.0


def test_extraction_duplicate_retry_idempotency(db_session, extraction_setup):
    project, _, capture = extraction_setup

    provider_json = json.dumps({
        "items": [
            {"type": "fact", "title": "Unique Fact", "content": "Fact description"},
        ]
    })
    provider = DummyAIProvider(response_text=provider_json)

    # First extraction
    res1 = extraction_service.extract_from_text(
        db_session,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text="Conversation text",
        source_capture_id=capture.id,
    )
    assert res1.inserted == 1

    # Second extraction (retry for same capture)
    res2 = extraction_service.extract_from_text(
        db_session,
        provider=provider,
        project_id=project.id,
        project_name=project.name,
        conversation_text="Conversation text",
        source_capture_id=capture.id,
    )
    # Deduplicated, 0 new items inserted
    assert res2.inserted == 0


def test_extraction_batch_atomicity_rollback(db_session, extraction_setup, monkeypatch):
    project, _, capture = extraction_setup

    provider_json = json.dumps({
        "items": [
            {"type": "fact", "title": "Item 1", "content": "Content 1"},
            {"type": "fact", "title": "Item 2", "content": "Content 2"},
        ]
    })
    provider = DummyAIProvider(response_text=provider_json)

    # Force failure on emit_event during 2nd item
    call_count = 0

    def failing_emit(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("Database error during second item event")
        from app.services.events import emit_event as real_emit
        return real_emit(*args, **kwargs)

    monkeypatch.setattr("app.services.extraction.emit_event", failing_emit)

    with pytest.raises(RuntimeError, match="Database error during second item"):
        extraction_service.extract_from_text(
            db_session,
            provider=provider,
            project_id=project.id,
            project_name=project.name,
            conversation_text="Conversation text",
            source_capture_id=capture.id,
        )

    # Invariant: Neither item 1 nor item 2 was committed
    items = db_session.execute(
        select(MemoryItem).where(MemoryItem.project_id == project.id)
    ).scalars().all()
    assert len(items) == 0
