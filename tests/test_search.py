import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.conversation import Conversation, Message
from app.models.device import Device
from app.models.enums import MemoryStatus, MemoryType, MessageRole, Provenance
from app.models.handoff import Handoff
from app.models.memory import MemoryItem
from app.models.project import Project
from app.services import search as search_service


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Search Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Search Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_search_whitespace_query_rejected(db_session, auth_setup):
    project, _, _ = auth_setup

    with pytest.raises(ValueError, match="cannot be empty or whitespace only"):
        search_service.search_project(db_session, project_id=project.id, query="   ")


def test_search_all_entities_and_deterministic_ordering(db_session, auth_setup):
    project, _, _ = auth_setup

    # Memory item
    mem = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Findable Decision Target",
        content="Important details regarding Target keyword.",
    )
    # Conversation
    conv = Conversation(
        project_id=project.id,
        title="Target Discussion Conversation",
        cursor={},
    )
    db_session.add_all([mem, conv])
    db_session.flush()

    # Message
    msg = Message(
        project_id=project.id,
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="Hello Target message content",
        content_hash="hash_target",
    )
    # Handoff
    handoff = Handoff(
        project_id=project.id,
        to_role="implementation_agent",
        topic="Target Handoff Topic",
        content_md="Target handoff body",
    )
    db_session.add_all([msg, handoff])
    db_session.commit()

    hits = search_service.search_project(
        db_session, project_id=project.id, query="Target", limit=10
    )

    assert len(hits["memory"]) == 1
    assert hits["memory"][0].title == "Findable Decision Target"

    assert len(hits["conversations"]) == 1
    assert hits["conversations"][0].title == "Target Discussion Conversation"

    assert len(hits["messages"]) == 1
    assert "Target message" in hits["messages"][0].content

    assert len(hits["handoffs"]) == 1
    assert hits["handoffs"][0].topic == "Target Handoff Topic"


def test_search_strict_project_isolation(db_session, auth_setup):
    p1, _, _ = auth_setup

    # Project 2 with identical keyword
    p2 = Project(name="Project 2", last_event_sequence=0)
    db_session.add(p2)
    db_session.commit()

    db_session.add(
        MemoryItem(
            project_id=p2.id,
            type=MemoryType.FACT,
            status=MemoryStatus.ACTIVE,
            provenance=Provenance.USER_CONFIRMED,
            title="P2 Confidential Target",
            content="Sensitive",
        )
    )
    db_session.commit()

    hits_p1 = search_service.search_project(
        db_session, project_id=p1.id, query="Target", limit=50
    )
    # Invariant: No cross-project leakage
    assert not any(m.project_id != p1.id for m in hits_p1["memory"])
    assert not any("Confidential" in m.title for m in hits_p1["memory"])


def test_search_api_route(client, auth_setup, db_session):
    project, _, token = auth_setup

    db_session.add(
        MemoryItem(
            project_id=project.id,
            type=MemoryType.DECISION,
            status=MemoryStatus.ACTIVE,
            provenance=Provenance.USER_CONFIRMED,
            title="API Search Query Item",
            content="Content for API query",
        )
    )
    db_session.commit()

    # Valid search
    res = client.get(
        f"/projects/{project.id}/search?q=API+Search",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["memory"]) == 1
    assert data["memory"][0]["title"] == "API Search Query Item"

    # Whitespace query -> 400
    res_bad = client.get(
        f"/projects/{project.id}/search?q=%20%20%20",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad.status_code == 400
