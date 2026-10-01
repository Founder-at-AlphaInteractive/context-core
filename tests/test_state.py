import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.device import Device
from app.models.enums import EventType
from app.models.event import Event
from app.models.project import Project
from app.services import projects as projects_service
from app.services import state as state_service
from app.services.state import StateVersionConflict, _deep_merge


def test_deep_merge_semantics():
    base = {
        "engine": "Unity",
        "features": ["terrain", "physics"],
        "nested": {"key1": "val1"},
    }
    patch = {
        "features": ["physics", "sound"],
        "nested": {"key2": "val2"},
        "status": "in_progress",
    }
    merged = _deep_merge(base, patch)

    # Base was not mutated!
    assert base["features"] == ["terrain", "physics"]
    assert "status" not in base

    # Merged has recursive dict merge
    assert merged["nested"] == {"key1": "val1", "key2": "val2"}
    assert merged["engine"] == "Unity"
    assert merged["status"] == "in_progress"

    # List concatenation with exact deduplication
    assert merged["features"] == ["terrain", "physics", "sound"]


def test_state_versioning_and_concurrency(db_session):
    p = projects_service.create_project(db_session, name="State Proj")
    dev = Device(name="Dev 1", platform="win", token_hash="dummy1", revoked=False)
    db_session.add(dev)
    db_session.commit()

    # Initial state (version 1)
    s1 = state_service.update_state(
        db_session,
        project=p,
        device=dev,
        patch={"phase": "planning"},
        note="Initial state",
    )
    assert s1.version == 1
    assert s1.state_json == {"phase": "planning"}

    # Patch update with correct expected_version (version 2)
    s2 = state_service.update_state(
        db_session,
        project=p,
        device=dev,
        patch={"engine": "Godot"},
        expected_version=1,
        note="Add engine",
    )
    assert s2.version == 2
    assert s2.state_json == {"phase": "planning", "engine": "Godot"}

    # Stale expected_version should raise StateVersionConflict
    with pytest.raises(StateVersionConflict) as exc:
        state_service.update_state(
            db_session,
            project=p,
            device=dev,
            patch={"phase": "development"},
            expected_version=1,  # actual is 2!
        )
    assert exc.value.expected == 1
    assert exc.value.actual == 2

    # Replace state entirely
    s3 = state_service.update_state(
        db_session,
        project=p,
        device=dev,
        patch={"wiped": True},
        expected_version=2,
        replace=True,
    )
    assert s3.version == 3
    assert s3.state_json == {"wiped": True}

    # History ordered by version desc
    history = state_service.list_state_history(db_session, p.id)
    assert len(history) == 3
    assert [s.version for s in history] == [3, 2, 1]


def test_state_project_isolation(db_session):
    p1 = projects_service.create_project(db_session, name="Proj 1")
    p2 = projects_service.create_project(db_session, name="Proj 2")
    dev = Device(name="Dev 1", platform="win", token_hash="dummy2", revoked=False)
    db_session.add(dev)
    db_session.commit()

    state_service.update_state(
        db_session, project=p1, device=dev, patch={"secret": "proj1_secret"}
    )
    state_service.update_state(
        db_session, project=p2, device=dev, patch={"secret": "proj2_secret"}
    )

    s1 = state_service.get_current_state(db_session, p1.id)
    s2 = state_service.get_current_state(db_session, p2.id)

    assert s1.state_json == {"secret": "proj1_secret"}
    assert s2.state_json == {"secret": "proj2_secret"}


def test_state_api_flow(client, db_session):
    token = generate_device_token()
    device = Device(
        name="State API Dev",
        platform="linux",
        token_hash=hash_device_token(token),
        revoked=False,
    )
    p = projects_service.create_project(db_session, name="State API Proj")
    db_session.add(device)
    db_session.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # No state yet
    resp = client.get(f"/projects/{p.id}/state", headers=headers)
    assert resp.status_code == 200
    assert resp.json() is None

    # Update state v1
    resp = client.put(
        f"/projects/{p.id}/state",
        json={"patch": {"phase": "prototype"}, "note": "v1"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1
    assert resp.json()["state_json"]["phase"] == "prototype"

    # Conflicting expected_version returns 409
    resp = client.put(
        f"/projects/{p.id}/state",
        json={"patch": {"phase": "alpha"}, "expected_version": 99},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["message"] == "State version conflict"

    # History
    resp = client.get(f"/projects/{p.id}/state/history", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_concurrent_state_updates_optimistic_conflict(engine, SessionFactory):
    """Verify that concurrent updates with same expected_version safely result in 1 success and 1 conflict."""
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-level FOR UPDATE concurrency requires PostgreSQL")
    import threading

    session0 = SessionFactory()
    p = projects_service.create_project(session0, name="Concurrency Test Project")
    dev = Device(name="Concurrent Dev", platform="linux", token_hash="dummy_concurrent", revoked=False)
    session0.add(dev)
    session0.commit()
    project_id = p.id
    dev_id = dev.id
    session0.close()

    barrier = threading.Barrier(2)
    results = {}

    def worker(worker_id, patch_data):
        session = SessionFactory()
        try:
            proj = session.get(Project, project_id)
            device = session.get(Device, dev_id)
            barrier.wait()
            state = state_service.update_state(
                session,
                project=proj,
                device=device,
                patch=patch_data,
                expected_version=0,
            )
            results[worker_id] = ("ok", state.version)
        except StateVersionConflict as exc:
            results[worker_id] = ("conflict", exc.actual)
        except Exception as exc:
            results[worker_id] = ("error", str(exc))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("writer_a", {"worker_a": True}))
    t2 = threading.Thread(target=worker, args=("writer_b", {"worker_b": True}))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one writer must succeed (v1), and the other must receive conflict
    outcomes = [r[0] for r in results.values()]
    assert "ok" in outcomes
    assert "conflict" in outcomes

    # Verify final state in database
    verify_session = SessionFactory()
    current = state_service.get_current_state(verify_session, project_id)
    assert current.version == 1
    history = state_service.list_state_history(verify_session, project_id)
    assert len(history) == 1
    verify_session.close()


def test_concurrent_state_updates_serialized_versions(engine, SessionFactory):
    """Verify that concurrent updates without expected_version serialize deterministically."""
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-level FOR UPDATE concurrency requires PostgreSQL")
    import threading

    session0 = SessionFactory()
    p = projects_service.create_project(session0, name="Serialization Test Project")
    dev = Device(name="Serial Dev", platform="linux", token_hash="dummy_serial", revoked=False)
    session0.add(dev)
    session0.commit()
    project_id = p.id
    dev_id = dev.id
    session0.close()

    barrier = threading.Barrier(2)
    results = []

    def worker(worker_id, patch_data):
        session = SessionFactory()
        try:
            proj = session.get(Project, project_id)
            device = session.get(Device, dev_id)
            barrier.wait()
            state = state_service.update_state(
                session,
                project=proj,
                device=device,
                patch=patch_data,
            )
            results.append((worker_id, state.version))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("w1", {"key1": "val1"}))
    t2 = threading.Thread(target=worker, args=("w2", {"key2": "val2"}))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    versions = [r[1] for r in results]
    assert sorted(versions) == [1, 2]

    # Verify state history
    verify_session = SessionFactory()
    history = state_service.list_state_history(verify_session, project_id)
    assert len(history) == 2
    assert [s.version for s in history] == [2, 1]
    final_state = state_service.get_current_state(verify_session, project_id)
    assert final_state.state_json.get("key1") == "val1"
    assert final_state.state_json.get("key2") == "val2"
    verify_session.close()

