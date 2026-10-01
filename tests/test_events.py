import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.enums import EventType
from app.models.event import Event
from app.models.project import Project
from app.services.events import EventEmitter, emit_event




def test_event_sequence_monotonicity(db_session):
    project = Project(
        name="Event Test Project",
        description="Testing event sequence",
        last_event_sequence=0,
    )
    db_session.add(project)
    db_session.commit()

    emitter = EventEmitter(db_session)
    e1 = emitter.emit(
        project_id=project.id,
        event_type=EventType.PROJECT_CREATED,
        payload={"name": "Event Test Project"},
    )
    assert e1.sequence == 1
    assert project.last_event_sequence == 1

    e2 = emitter.emit(
        project_id=project.id,
        event_type=EventType.PROJECT_UPDATED,
        payload={"field": "updated"},
    )
    assert e2.sequence == 2
    assert project.last_event_sequence == 2

    e3 = emit_event(
        db_session,
        project_id=project.id,
        event_type=EventType.SYNC_REQUIRED,
    )
    assert e3.sequence == 3
    assert project.last_event_sequence == 3


def test_independent_project_event_sequences(db_session):
    project_a = Project(name="Project A", last_event_sequence=0)
    project_b = Project(name="Project B", last_event_sequence=0)
    db_session.add_all([project_a, project_b])
    db_session.commit()

    emitter = EventEmitter(db_session)

    # Project A event
    ea1 = emitter.emit(project_id=project_a.id, event_type=EventType.PROJECT_CREATED)
    assert ea1.sequence == 1

    # Project B event starts at 1
    eb1 = emitter.emit(project_id=project_b.id, event_type=EventType.PROJECT_CREATED)
    assert eb1.sequence == 1

    # Project A event increments to 2
    ea2 = emitter.emit(project_id=project_a.id, event_type=EventType.PROJECT_UPDATED)
    assert ea2.sequence == 2

    # Project B remains at 1
    assert project_b.last_event_sequence == 1
    assert project_a.last_event_sequence == 2


def test_emit_event_nonexistent_project_fails(db_session):
    emitter = EventEmitter(db_session)
    random_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        emitter.emit(project_id=random_id, event_type=EventType.PROJECT_CREATED)


def test_event_transactional_rollback(db_session):
    project = Project(name="Rollback Project", last_event_sequence=0)
    db_session.add(project)
    db_session.commit()

    emitter = EventEmitter(db_session)
    emitter.emit(
        project_id=project.id,
        event_type=EventType.PROJECT_CREATED,
    )
    # Rollback transaction
    db_session.rollback()

    # Verify event was not persisted and sequence was not incremented
    events = db_session.query(Event).filter(Event.project_id == project.id).all()
    assert len(events) == 0
    refreshed_project = db_session.get(Project, project.id)
    assert refreshed_project.last_event_sequence == 0


def test_events_endpoint_catchup_and_isolation(client, db_session):
    from app.auth.security import generate_device_token, hash_device_token
    from app.models.device import Device

    # Setup 2 projects
    p1 = Project(name="Events P1", last_event_sequence=0)
    p2 = Project(name="Events P2", last_event_sequence=0)
    db_session.add_all([p1, p2])

    raw_token = generate_device_token()
    device = Device(
        name="Event Reader Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(p1)
    db_session.refresh(p2)

    headers = {"Authorization": f"Bearer {raw_token}"}

    # Emit 3 events in P1 and 2 events in P2
    emitter = EventEmitter(db_session)
    e1 = emitter.emit(project_id=p1.id, event_type=EventType.PROJECT_CREATED, payload={"meta": 1})
    e2 = emitter.emit(project_id=p1.id, event_type=EventType.MEMORY_CREATED, payload={"meta": 2})
    e3 = emitter.emit(project_id=p1.id, event_type=EventType.DECISION_CREATED, payload={"meta": 3})

    emitter.emit(project_id=p2.id, event_type=EventType.PROJECT_CREATED, payload={"p2_secret": True})
    emitter.emit(project_id=p2.id, event_type=EventType.PROJECT_UPDATED, payload={"p2_secret": True})
    db_session.commit()

    # 1. Catch-up from beginning (since=0) for P1
    res1 = client.get(f"/projects/{p1.id}/events?since=0", headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["latest_sequence"] == 3
    assert len(data1["events"]) == 3
    assert [e["sequence"] for e in data1["events"]] == [1, 2, 3]

    # Verify no cross-project leakage from P2
    assert all(e["project_id"] == str(p1.id) for e in data1["events"])
    assert not any("p2_secret" in e["payload"] for e in data1["events"])

    # 2. Catch-up with since=1 (client already saw sequence 1)
    res2 = client.get(f"/projects/{p1.id}/events?since=1", headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["latest_sequence"] == 3
    assert len(data2["events"]) == 2
    assert [e["sequence"] for e in data2["events"]] == [2, 3]

    # 3. Catch-up with since=3 (fully caught up)
    res3 = client.get(f"/projects/{p1.id}/events?since=3", headers=headers)
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["latest_sequence"] == 3
    assert len(data3["events"]) == 0

    # 4. Access P2 events
    res_p2 = client.get(f"/projects/{p2.id}/events?since=0", headers=headers)
    assert res_p2.status_code == 200
    assert res_p2.json()["latest_sequence"] == 2
    assert len(res_p2.json()["events"]) == 2

