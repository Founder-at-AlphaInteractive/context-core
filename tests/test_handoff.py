import uuid
import pytest
from sqlalchemy import select

from app.auth.security import generate_device_token, hash_device_token
from app.models.ai_profile import AIProfile, ProjectAIRole
from app.models.device import Device
from app.models.enums import AIProfileType, EventType
from app.models.event import Event
from app.models.handoff import ContextSnapshot, Handoff
from app.models.project import Project
from app.services import handoff as handoff_service


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Handoff Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Handoff Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)

    # Valid AI Profiles
    profile1 = AIProfile(name="Profile Arch", provider="test_provider", type=AIProfileType.API)
    profile2 = AIProfile(name="Profile Code", provider="test_provider", type=AIProfileType.API)
    db_session.add_all([profile1, profile2])
    db_session.commit()

    db_session.refresh(project)
    db_session.refresh(device)
    db_session.refresh(profile1)
    db_session.refresh(profile2)
    return project, device, profile1, profile2, raw_token


def test_successful_handoff_atomic_snapshot_and_event(db_session, auth_setup):
    project, device, p1, p2, _ = auth_setup

    handoff = handoff_service.generate_handoff(
        db_session,
        project=project,
        to_role="implementation_agent",
        to_ai_profile_id=p2.id,
        from_role="architecture",
        from_ai_profile_id=p1.id,
        topic="Database migration task",
        task="Write Alembic migration 0003",
        token_budget=1000,
        client_id=device.id,
    )

    assert handoff.id is not None
    assert handoff.topic == "Database migration task"
    assert handoff.context_snapshot_id is not None

    # Check snapshot exists
    snapshot = db_session.get(ContextSnapshot, handoff.context_snapshot_id)
    assert snapshot is not None
    assert snapshot.role == "implementation_agent"
    assert snapshot.token_count <= 1000

    # Check HANDOFF_CREATED event was emitted
    event = db_session.execute(
        select(Event).where(Event.entity_id == handoff.id)
    ).scalar_one_or_none()
    assert event is not None
    assert event.type == EventType.HANDOFF_CREATED
    assert event.payload["to_role"] == "implementation_agent"


def test_handoff_rollback_leaves_no_orphaned_snapshot(db_session, auth_setup, monkeypatch):
    project, device, p1, p2, _ = auth_setup

    def failing_emit(*args, **kwargs):
        raise RuntimeError("Failure during handoff event emission")

    monkeypatch.setattr("app.services.handoff.emit_event", failing_emit)

    with pytest.raises(RuntimeError, match="Failure during handoff event emission"):
        handoff_service.generate_handoff(
            db_session,
            project=project,
            to_role="implementation_agent",
            to_ai_profile_id=p2.id,
            from_role="architecture",
            from_ai_profile_id=p1.id,
            topic="Atomic Rollback Test",
            task="Should fail cleanly",
            token_budget=1000,
        )

    db_session.rollback()

    # Invariant: Neither handoff nor snapshot may exist after failure
    handoffs = db_session.execute(
        select(Handoff).where(Handoff.topic == "Atomic Rollback Test")
    ).scalars().all()
    assert len(handoffs) == 0

    snapshots = db_session.execute(
        select(ContextSnapshot).where(ContextSnapshot.task == "Should fail cleanly")
    ).scalars().all()
    assert len(snapshots) == 0


def test_handoff_validation_invalid_ai_profiles(db_session, auth_setup):
    project, _, p1, _, _ = auth_setup

    # Non-existent target AI profile
    with pytest.raises(ValueError, match="Target AI Profile .* not found"):
        handoff_service.generate_handoff(
            db_session,
            project=project,
            to_role="implementation_agent",
            to_ai_profile_id=uuid.uuid4(),
            from_role="architecture",
            from_ai_profile_id=p1.id,
            topic="Test",
            task=None,
            token_budget=1000,
        )

    # Non-existent source AI profile
    with pytest.raises(ValueError, match="Source AI Profile .* not found"):
        handoff_service.generate_handoff(
            db_session,
            project=project,
            to_role="implementation_agent",
            to_ai_profile_id=p1.id,
            from_role="architecture",
            from_ai_profile_id=uuid.uuid4(),
            topic="Test",
            task=None,
            token_budget=1000,
        )


def test_handoff_cross_project_profile_role_rejected(db_session, auth_setup):
    project1, _, p1, _, _ = auth_setup

    # Project 2 with an assigned AI profile role
    project2 = Project(name="Project 2", last_event_sequence=0)
    p2_profile = AIProfile(name="P2 Profile", provider="p2_provider", type=AIProfileType.API)
    db_session.add_all([project2, p2_profile])
    db_session.commit()

    p2_role = ProjectAIRole(
        project_id=project2.id,
        ai_profile_id=p2_profile.id,
        role_name="exclusive_role",
    )
    db_session.add(p2_role)
    db_session.commit()

    # Attempting to assign project 2's exclusive profile role to project 1
    with pytest.raises(ValueError, match="belongs to a different project"):
        handoff_service.generate_handoff(
            db_session,
            project=project1,
            to_role="exclusive_role",
            to_ai_profile_id=p2_profile.id,
            from_role="architecture",
            from_ai_profile_id=p1.id,
            topic="Cross project hijack attempt",
            task=None,
            token_budget=1000,
        )


def test_handoff_api_endpoints(client, auth_setup):
    project, _, p1, p2, token = auth_setup

    # 1. Create handoff via API
    res_create = client.post(
        f"/projects/{project.id}/handoffs",
        json={
            "to_role": "implementation_agent",
            "to_ai_profile_id": str(p2.id),
            "from_role": "architecture",
            "from_ai_profile_id": str(p1.id),
            "topic": "API Handoff Flow",
            "task": "Build search feature",
            "token_budget": 800,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_create.status_code == 201
    h_data = res_create.json()
    assert h_data["topic"] == "API Handoff Flow"
    h_id = h_data["id"]

    # 2. List handoffs
    res_list = client.get(
        f"/projects/{project.id}/handoffs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 3. Read single handoff
    res_read = client.get(
        f"/projects/{project.id}/handoffs/{h_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_read.status_code == 200
    assert res_read.json()["id"] == h_id
