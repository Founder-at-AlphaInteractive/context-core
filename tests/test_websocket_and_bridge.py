"""Tests for WebSocket manager, bridge, transaction hooks, and endpoint."""

import asyncio
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth.security import generate_device_token, hash_device_token
from app.db.session import _PENDING_STACK_KEY
from app.models.device import Device
from app.models.enums import EventType
from app.models.project import Project
from app.services.events import emit_event
from app.websocket import bridge
from app.websocket.manager import ConnectionManager, get_manager


@pytest.fixture
def ws_project(db_session):
    project = Project(id=uuid.uuid4(), name="WS Project", last_event_sequence=0)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def active_device(db_session):
    raw_token = generate_device_token()
    device = Device(
        name="WS Active Device",
        platform="darwin",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)
    return device, raw_token


@pytest.mark.asyncio
async def test_connection_manager_subscribe_and_broadcast():
    manager = ConnectionManager()
    project_id = uuid.uuid4()

    q1 = await manager.subscribe(project_id)
    q2 = await manager.subscribe(project_id)
    assert manager.subscriber_count(project_id) == 2

    event = {"project_id": str(project_id), "type": "memory_created", "sequence": 1}
    await manager.broadcast(project_id, event)

    res1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    res2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert res1 == event
    assert res2 == event

    await manager.unsubscribe(project_id, q1)
    assert manager.subscriber_count(project_id) == 1

    await manager.unsubscribe(project_id, q2)
    assert manager.subscriber_count(project_id) == 0


@pytest.mark.asyncio
async def test_connection_manager_queue_overflow_signals_sync_required():
    manager = ConnectionManager()
    project_id = uuid.uuid4()

    q = await manager.subscribe(project_id)
    # Fill the queue to capacity
    while not q.full():
        q.put_nowait({"filler": True})

    # Broadcast on full queue should push sync_required
    await manager.broadcast(project_id, {"type": "new_event", "project_id": str(project_id)})

    # Drain to find sync_required notification
    found_sync = False
    while not q.empty():
        msg = q.get_nowait()
        if msg.get("type") == "sync_required":
            found_sync = True
            assert msg["reason"] == "queue_overflow"
            assert msg["project_id"] == str(project_id)
            break

    assert found_sync is True
    await manager.unsubscribe(project_id, q)


def test_transaction_hooks_commit_publishes_events(db_session, ws_project, monkeypatch):
    published = []

    def mock_publish(payload):
        published.append(payload)

    monkeypatch.setattr(bridge, "publish_event_sync", mock_publish)

    emit_event(
        db_session,
        project_id=ws_project.id,
        event_type=EventType.PROJECT_UPDATED,
        payload={"field": "name"},
    )
    # Before commit, nothing published
    assert len(published) == 0

    db_session.commit()
    # After commit, event published
    assert len(published) == 1
    assert published[0]["type"] == EventType.PROJECT_UPDATED.value
    assert published[0]["sequence"] == 1


def test_transaction_hooks_rollback_discards_events(db_session, ws_project, monkeypatch):
    published = []

    def mock_publish(payload):
        published.append(payload)

    monkeypatch.setattr(bridge, "publish_event_sync", mock_publish)

    emit_event(
        db_session,
        project_id=ws_project.id,
        event_type=EventType.PROJECT_UPDATED,
        payload={"field": "name"},
    )
    db_session.rollback()

    assert len(published) == 0
    # Pending stack should be cleared / contain no events
    stack = db_session.info.get(_PENDING_STACK_KEY, [])
    all_pending = [ev for frame in stack for ev in frame]
    assert len(all_pending) == 0


def test_nested_transaction_savepoint_rollback_preserves_outer_events(
    db_session, ws_project, monkeypatch
):
    """Critical invariant: savepoint rollback must NOT discard outer transaction events."""
    published = []

    def mock_publish(payload):
        published.append(payload)

    monkeypatch.setattr(bridge, "publish_event_sync", mock_publish)

    # 1. Outer event
    emit_event(
        db_session,
        project_id=ws_project.id,
        event_type=EventType.PROJECT_UPDATED,
        payload={"step": "outer_1"},
    )

    # 2. Savepoint event that gets rolled back
    sp = db_session.begin_nested()
    emit_event(
        db_session,
        project_id=ws_project.id,
        event_type=EventType.MEMORY_CREATED,
        payload={"step": "savepoint_dropped"},
    )
    sp.rollback()

    # 3. Outer event after savepoint
    emit_event(
        db_session,
        project_id=ws_project.id,
        event_type=EventType.TASK_CREATED,
        payload={"step": "outer_2"},
    )

    db_session.commit()

    # ONLY outer_1 and outer_2 should be published! Savepoint event was discarded.
    assert len(published) == 2
    steps = [p["payload"]["step"] for p in published]
    assert steps == ["outer_1", "outer_2"]


def test_websocket_endpoint_auth_and_hello(client, ws_project, active_device):
    device, raw_token = active_device

    # Connect with Authorization Bearer header
    with client.websocket_connect(
        f"/projects/{ws_project.id}/ws",
        headers={"Authorization": f"Bearer {raw_token}"},
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["project_id"] == str(ws_project.id)
        assert hello["device_id"] == str(device.id)


def test_websocket_endpoint_query_token_auth(client, ws_project, active_device):
    device, raw_token = active_device

    # Connect with query parameter
    with client.websocket_connect(
        f"/projects/{ws_project.id}/ws?token={raw_token}"
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["project_id"] == str(ws_project.id)
        assert hello["device_id"] == str(device.id)


def test_websocket_endpoint_invalid_token_rejected(client, ws_project):
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect(
        f"/projects/{ws_project.id}/ws",
        headers={"Authorization": "Bearer invalid-token"},
    ) as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "Invalid or revoked" in err["detail"]
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def test_websocket_endpoint_revoked_token_rejected(client, ws_project, db_session):
    from starlette.websockets import WebSocketDisconnect

    raw_token = generate_device_token()
    device = Device(
        name="Revoked Device",
        platform="linux",
        token_hash=hash_device_token(raw_token),
        revoked=True,
    )
    db_session.add(device)
    db_session.commit()

    with client.websocket_connect(
        f"/projects/{ws_project.id}/ws",
        headers={"Authorization": f"Bearer {raw_token}"},
    ) as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "Invalid or revoked" in err["detail"]
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def test_websocket_endpoint_missing_project_rejected(client, active_device):
    from starlette.websockets import WebSocketDisconnect

    _, raw_token = active_device
    missing_id = uuid.uuid4()

    with client.websocket_connect(
        f"/projects/{missing_id}/ws",
        headers={"Authorization": f"Bearer {raw_token}"},
    ) as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "Project not found" in err["detail"]
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008
