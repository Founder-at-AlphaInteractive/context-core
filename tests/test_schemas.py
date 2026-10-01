import uuid
import pytest
from pydantic import ValidationError

from app.models.enums import AIProfileType, CaptureMode, MemoryStatus, MemoryType, MessageRole, Provenance
from app.schemas.auth import DeviceRegisterRequest
from app.schemas.capture import CaptureMessageInput, CaptureRequest
from app.schemas.memory import MemoryItemCreate
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.schemas.state import ProjectStateUpdate


def test_project_create_validation():
    # Valid
    p = ProjectCreate(name="Context Core", description="Persistent Reality Engine")
    assert p.name == "Context Core"

    # Empty name fails
    with pytest.raises(ValidationError):
        ProjectCreate(name="")

    # Name too long fails
    with pytest.raises(ValidationError):
        ProjectCreate(name="x" * 201)


def test_memory_item_create_validation():
    # Valid
    mem = MemoryItemCreate(
        type=MemoryType.DECISION,
        title="Architecture Decision",
        content="Use FastAPI + PostgreSQL",
        provenance=Provenance.USER_CONFIRMED,
        confidence=0.95,
        tags=["architecture", "backend"],
    )
    assert mem.confidence == 0.95
    assert mem.tags == ["architecture", "backend"]

    # Invalid confidence (< 0 or > 1)
    with pytest.raises(ValidationError):
        MemoryItemCreate(
            type=MemoryType.DECISION,
            title="Invalid",
            content="Invalid",
            confidence=1.5,
        )

    with pytest.raises(ValidationError):
        MemoryItemCreate(
            type=MemoryType.DECISION,
            title="Invalid",
            content="Invalid",
            confidence=-0.1,
        )


def test_capture_request_validation():
    client_id = uuid.uuid4()
    local_id = uuid.uuid4()

    req = CaptureRequest(
        client_id=client_id,
        local_id=local_id,
        mode=CaptureMode.LAST_EXCHANGE,
        messages=[
            CaptureMessageInput(role=MessageRole.USER, content="Hello"),
            CaptureMessageInput(role=MessageRole.ASSISTANT, content="Hi there"),
        ],
    )
    assert req.client_id == client_id
    assert len(req.messages) == 2

    # Empty message content
    with pytest.raises(ValidationError):
        CaptureMessageInput(role=MessageRole.USER, content="")
