import json
import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.ai_profile import AIProfile, ProjectAIRole
from app.models.conversation import Capture, Conversation, Message
from app.models.device import Device
from app.models.enums import AIProfileType, CaptureMode, EventType, MemoryStatus, MemoryType, MessageRole, Provenance
from app.models.handoff import ContextSnapshot, Handoff
from app.models.memory import MemoryItem
from app.models.project import Project, ProjectState
from app.services import export as export_service
from app.services.events import emit_event


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Export Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Export Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_export_empty_project(db_session, auth_setup):
    project, _, _ = auth_setup

    data = export_service.export_project(db_session, project)

    assert data["schema_version"] == 1
    assert data["project"]["name"] == "Export Test Project"
    assert data["project"]["id"] == str(project.id)
    assert len(data["memory_items"]) == 0
    assert len(data["conversations"]) == 0
    assert len(data["events"]) == 0

    # JSON serializability check
    serialized = json.dumps(data)
    assert len(serialized) > 0


def test_export_complete_project_with_all_entities_and_security(db_session, auth_setup):
    project, device, _ = auth_setup

    # 1. State
    state = ProjectState(
        project_id=project.id,
        version=1,
        state_json={"phase": "Development"},
        note="Initial state",
    )
    # 2. AI Profile & Role
    profile = AIProfile(name="Export Profile", provider="groq", type=AIProfileType.API)
    db_session.add_all([state, profile])
    db_session.flush()

    role = ProjectAIRole(
        project_id=project.id,
        ai_profile_id=profile.id,
        role_name="architect",
    )
    # 3. Conversation, Message, Capture
    conv = Conversation(project_id=project.id, title="Export Conv", cursor={})
    db_session.add_all([role, conv])
    db_session.flush()

    msg = Message(
        project_id=project.id,
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="Export message",
        content_hash="h1",
    )
    cap = Capture(
        project_id=project.id,
        conversation_id=conv.id,
        client_id=device.id,
        local_id=uuid.uuid4(),
        mode=CaptureMode.LAST_EXCHANGE,
        payload={"msg": "test"},
    )
    # 4. Memory Item
    mem = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Export Decision",
        content="Export details",
    )
    # 5. Context Snapshot & Handoff
    snap = ContextSnapshot(
        project_id=project.id,
        role="architect",
        content_md="# Context",
        token_count=10,
    )
    db_session.add_all([msg, cap, mem, snap])
    db_session.flush()

    handoff = Handoff(
        project_id=project.id,
        to_role="architect",
        topic="Export Handoff",
        content_md="# Handoff",
        context_snapshot_id=snap.id,
    )
    db_session.add(handoff)

    # 6. Events in sequence
    emit_event(db_session, project_id=project.id, event_type=EventType.PROJECT_CREATED, payload={"step": 1})
    emit_event(db_session, project_id=project.id, event_type=EventType.PROJECT_UPDATED, payload={"step": 2})
    db_session.commit()

    # Perform Export
    exported = export_service.export_project(db_session, project)

    # Schema version
    assert exported["schema_version"] == 1

    # Security check: MUST NOT contain device secrets, tokens, or device table
    serialized = json.dumps(exported)
    assert "token_hash" not in serialized
    assert "devices" not in exported

    # Relationships and entities check
    assert len(exported["project_states"]) == 1
    assert exported["project_states"][0]["version"] == 1

    assert len(exported["project_ai_roles"]) == 1
    assert exported["project_ai_roles"][0]["role_name"] == "architect"

    assert len(exported["conversations"]) == 1
    assert len(exported["messages"]) == 1
    assert len(exported["captures"]) == 1
    assert len(exported["memory_items"]) == 1
    assert len(exported["handoffs"]) == 1
    assert len(exported["context_snapshots"]) == 1

    # Events strictly ordered by ascending sequence
    assert len(exported["events"]) == 2
    assert exported["events"][0]["sequence"] == 1
    assert exported["events"][1]["sequence"] == 2
    assert exported["events"][0]["type"] == EventType.PROJECT_CREATED.value


def test_export_api_route(client, auth_setup):
    project, _, token = auth_setup

    res = client.get(
        f"/projects/{project.id}/export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    assert "attachment; filename=" in res.headers["content-disposition"]
    data = res.json()
    assert data["schema_version"] == 1
    assert data["project"]["name"] == "Export Test Project"
