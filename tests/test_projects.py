import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.device import Device
from app.models.enums import EventType
from app.models.event import Event
from app.models.project import Project
from app.services import projects as projects_service


def test_project_service_create_and_events(db_session):
    p = projects_service.create_project(
        db_session, name="Alpha Project", description="Main Project"
    )
    assert p.id is not None
    assert p.name == "Alpha Project"
    assert p.last_event_sequence == 1

    events = db_session.query(Event).filter(Event.project_id == p.id).all()
    assert len(events) == 1
    assert events[0].type == EventType.PROJECT_CREATED
    assert events[0].payload["name"] == "Alpha Project"


def test_project_service_update_and_noop(db_session):
    p = projects_service.create_project(
        db_session, name="Beta Project", description="Initial"
    )
    assert p.last_event_sequence == 1

    # No-op update (same name and same description)
    p = projects_service.update_project(
        db_session, p, name="Beta Project", description="Initial"
    )
    assert p.last_event_sequence == 1  # No event emitted

    # Actual update
    p = projects_service.update_project(
        db_session, p, name="Beta Updated", description="New Desc"
    )
    assert p.name == "Beta Updated"
    assert p.description == "New Desc"
    assert p.last_event_sequence == 2

    # Clear description
    p = projects_service.update_project(
        db_session, p, clear_description=True
    )
    assert p.description is None
    assert p.last_event_sequence == 3


def test_project_service_delete(db_session):
    p = projects_service.create_project(db_session, name="To Delete")
    project_id = p.id
    projects_service.delete_project(db_session, p)

    assert projects_service.get_project(db_session, project_id) is None


def test_project_api_crud(client, db_session):
    token = generate_device_token()
    device = Device(
        name="Test Dev",
        platform="win",
        token_hash=hash_device_token(token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # Create
    resp = client.post(
        "/projects",
        json={"name": "API Project", "description": "Via API"},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    project_id = data["id"]
    assert data["name"] == "API Project"

    # Read
    resp = client.get(f"/projects/{project_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == project_id

    # List
    resp = client.get("/projects", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Update (patch)
    resp = client.patch(
        f"/projects/{project_id}",
        json={"name": "Renamed API Project"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed API Project"

    # Delete
    resp = client.delete(f"/projects/{project_id}", headers=headers)
    assert resp.status_code == 204

    # Verify deleted
    resp = client.get(f"/projects/{project_id}", headers=headers)
    assert resp.status_code == 404
