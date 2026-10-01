import uuid
import pytest
from sqlalchemy import select

from app.auth.security import generate_device_token, hash_device_token
from app.models.device import Device
from app.models.enums import EventType, MemoryStatus, MemoryType, Provenance
from app.models.event import Event
from app.models.memory import MemoryItem
from app.models.project import Project
from app.schemas.memory import MemoryItemCreate, MemoryItemUpdate
from app.services import memory as memory_service


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Memory Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Test Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_user_authoritative_creation_defaults_to_active(client, auth_setup, db_session):
    project, device, token = auth_setup

    payload = {
        "type": "decision",
        "title": "Database Engine",
        "content": "We will use PostgreSQL exclusively.",
        "provenance": "user_confirmed",
        "key": "db_engine",
        "value": "PostgreSQL",
    }

    res = client.post(
        f"/projects/{project.id}/memory",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    data = res.json()["memory"]
    assert data["status"] == "active"
    assert data["provenance"] == "user_confirmed"

    # Check event emitted atomically
    events = (
        db_session.execute(select(Event).where(Event.project_id == project.id).order_by(Event.sequence.asc()))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].type == EventType.DECISION_CREATED
    # Minimal payload verification: no full content leaked in event
    assert "content" not in events[0].payload
    assert events[0].payload["key"] == "db_engine"
    assert events[0].payload["status"] == "active"


def test_ai_proposed_creation_defaults_to_proposed(client, auth_setup):
    project, device, token = auth_setup

    payload = {
        "type": "requirement",
        "title": "Caching Requirement",
        "content": "AI suggests adding Redis caching.",
        "provenance": "ai_proposal",
        "key": "cache_system",
        "value": "Redis",
    }

    res = client.post(
        f"/projects/{project.id}/memory",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    data = res.json()["memory"]
    assert data["status"] == "proposed"
    assert data["provenance"] == "ai_proposal"


def test_caller_cannot_manufacture_active_status_for_ai_proposal(client, auth_setup):
    project, device, token = auth_setup

    # Caller attempts to create an AI proposal directly as ACTIVE
    payload = {
        "type": "decision",
        "title": "Manufactured Authority",
        "content": "AI claims this is active truth without human confirmation.",
        "provenance": "ai_proposal",
        "status": "active",  # Illegal for AI provenance!
        "key": "auth_rule",
        "value": "fake",
    }

    res = client.post(
        f"/projects/{project.id}/memory",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "cannot be created directly in ACTIVE status" in res.json()["detail"]


def test_legal_status_transitions(client, auth_setup, db_session):
    project, device, token = auth_setup

    # 1. Create AI proposal
    item, _ = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.REQUIREMENT,
            title="Export Format",
            content="Support JSON export",
            provenance=Provenance.AI_PROPOSAL,
            key="export_fmt",
            value="JSON",
        ),
    )
    assert item.status == MemoryStatus.PROPOSED

    # 2. Promote to ACTIVE via human confirmation (upgrade provenance + status)
    patch_res = client.patch(
        f"/projects/{project.id}/memory/{item.id}",
        json={
            "status": "active",
            "provenance": "user_confirmed",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()["memory"]
    assert updated["status"] == "active"
    assert updated["provenance"] == "user_confirmed"


def test_illegal_status_transitions_rejected(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Create ACTIVE user-confirmed decision
    item, _ = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.DECISION,
            title="Core Tech",
            content="FastAPI is our framework.",
            provenance=Provenance.USER_CONFIRMED,
            status=MemoryStatus.ACTIVE,
        ),
    )

    # 1. Downgrading ACTIVE to PROPOSED must be rejected
    res1 = client.patch(
        f"/projects/{project.id}/memory/{item.id}",
        json={"status": "proposed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 400
    assert "cannot be downgraded to PROPOSED" in res1.json()["detail"]

    # 2. Downgrading provenance from user_confirmed to ai_proposal must be rejected
    res2 = client.patch(
        f"/projects/{project.id}/memory/{item.id}",
        json={"provenance": "ai_proposal"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 400
    assert "Cannot downgrade memory provenance" in res2.json()["detail"]


def test_ai_proposal_cannot_silently_become_active_without_confirmation(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Create AI proposal
    item, _ = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.FACT,
            title="Server Spec",
            content="Estimated 8GB RAM",
            provenance=Provenance.AI_PROPOSAL,
        ),
    )

    # Try to set status=active without providing authoritative provenance
    res = client.patch(
        f"/projects/{project.id}/memory/{item.id}",
        json={"status": "active"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "Cannot promote memory with provenance 'ai_proposal' to ACTIVE" in res.json()["detail"]


def test_supersession_same_project_and_authority(client, auth_setup, db_session):
    project, device, token = auth_setup

    # 1. Old decision
    old_item, _ = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.DECISION,
            title="API Framework V1",
            content="Flask",
            provenance=Provenance.USER_CONFIRMED,
            key="framework",
            value="Flask",
        ),
    )
    assert old_item.status == MemoryStatus.ACTIVE

    # 2. Replacement decision superseding old one with equal authority
    new_res = client.post(
        f"/projects/{project.id}/memory",
        json={
            "type": "decision",
            "title": "API Framework V2",
            "content": "FastAPI",
            "provenance": "user_confirmed",
            "key": "framework",
            "value": "FastAPI",
            "supersedes_id": str(old_item.id),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert new_res.status_code == 201

    # Verify old item became SUPERSEDED
    db_session.refresh(old_item)
    assert old_item.status == MemoryStatus.SUPERSEDED


def test_weaker_provenance_cannot_supersede_authoritative_memory(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Authoritative active decision
    authoritative, _ = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.DECISION,
            title="Authoritative Target",
            content="Target content",
            provenance=Provenance.USER_CONFIRMED,
        ),
    )

    # Weaker AI proposal attempts to supersede it
    res = client.post(
        f"/projects/{project.id}/memory",
        json={
            "type": "decision",
            "title": "Weaker Replacement",
            "content": "Proposal content",
            "provenance": "ai_proposal",
            "supersedes_id": str(authoritative.id),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "Cannot supersede memory item with higher authority" in res.json()["detail"]


def test_cross_project_supersession_rejected(client, auth_setup, db_session):
    project1, device, token = auth_setup

    # Project 2 with an item
    project2 = Project(name="Project 2", last_event_sequence=0)
    db_session.add(project2)
    db_session.commit()

    p2_item, _ = memory_service.create_memory(
        db_session,
        project_id=project2.id,
        payload=MemoryItemCreate(
            type=MemoryType.DECISION,
            title="P2 Decision",
            content="P2 details",
            provenance=Provenance.USER_CONFIRMED,
        ),
    )

    # Project 1 attempts to supersede P2 item
    res = client.post(
        f"/projects/{project1.id}/memory",
        json={
            "type": "decision",
            "title": "Cross Project Attempt",
            "content": "Hijack attempt",
            "provenance": "user_confirmed",
            "supersedes_id": str(p2_item.id),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "Cannot supersede memory item from a different project" in res.json()["detail"]


def test_conflict_detection_and_self_conflict_prevention(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Create first decision: key="db", value="Postgres"
    item1, conflicts1 = memory_service.create_memory(
        db_session,
        project_id=project.id,
        payload=MemoryItemCreate(
            type=MemoryType.DECISION,
            title="DB Choice",
            content="Postgres",
            provenance=Provenance.USER_CONFIRMED,
            key="db",
            value="Postgres",
        ),
    )
    assert len(conflicts1) == 0

    # Create competing decision: key="db", value="MySQL"
    res2 = client.post(
        f"/projects/{project.id}/memory",
        json={
            "type": "decision",
            "title": "Conflicting DB",
            "content": "MySQL",
            "provenance": "ai_proposal",
            "key": "db",
            "value": "MySQL",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    conflicts2 = res2.json()["conflicts"]
    assert len(conflicts2) >= 1
    # Contradiction detected
    assert any(c["kind"] == "decision_contradiction" for c in conflicts2)

    # Verify updating item1 without changing conflicting value does NOT conflict with itself
    res_update = client.patch(
        f"/projects/{project.id}/memory/{item1.id}",
        json={"title": "Updated DB Choice Title"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_update.status_code == 200


def test_list_and_read_memory_isolation(client, auth_setup, db_session):
    project1, device, token = auth_setup

    # Project 2
    project2 = Project(name="Project 2", last_event_sequence=0)
    db_session.add(project2)
    db_session.commit()

    # Create in Project 1
    res1 = client.post(
        f"/projects/{project1.id}/memory",
        json={
            "type": "fact",
            "title": "P1 Secret Fact",
            "content": "Fact 1",
            "provenance": "user_confirmed",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    mem_id = res1.json()["memory"]["id"]

    # Read from Project 1 -> 200
    res_p1 = client.get(
        f"/projects/{project1.id}/memory/{mem_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_p1.status_code == 200

    # Read from Project 2 -> 404
    res_p2 = client.get(
        f"/projects/{project2.id}/memory/{mem_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_p2.status_code == 404

    # List Project 2 -> 0 items
    list_p2 = client.get(
        f"/projects/{project2.id}/memory",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_p2.status_code == 200
    assert len(list_p2.json()) == 0
