"""Tests for strict multi-project isolation."""

import uuid
import pytest


def _mk_project(client, headers, name: str) -> dict:
    resp = client.post("/projects", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_project_memory_and_events_are_isolated(client, auth_headers):
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    # Add memory to Project A
    m_resp = client.post(
        f"/projects/{a['id']}/memory",
        json={
            "type": "decision",
            "title": "A-only decision",
            "content": "belongs strictly to A",
            "provenance": "user_confirmed",
        },
        headers=auth_headers,
    )
    assert m_resp.status_code == 201

    # Project B's memory list must be completely empty
    b_memory = client.get(
        f"/projects/{b['id']}/memory", headers=auth_headers
    ).json()
    assert b_memory == []

    # Project B's events must contain ONLY events for Project B
    b_events = client.get(
        f"/projects/{b['id']}/events", headers=auth_headers
    ).json()["events"]
    assert len(b_events) >= 1
    assert all(e["project_id"] == b["id"] for e in b_events)


def test_capture_rejects_cross_project_conversation(client, auth_headers, device_token):
    device_id, _ = device_token
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    cap = client.post(
        f"/projects/{a['id']}/captures",
        json={
            "client_id": str(device_id),
            "local_id": str(uuid.uuid4()),
            "mode": "last_exchange",
            "external_thread_id": "shared-thread",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        },
        headers=auth_headers,
    ).json()
    conv_id = cap["conversation_id"]

    # Attempt to attach a capture in project B referencing project A's conversation id
    bad = client.post(
        f"/projects/{b['id']}/captures",
        json={
            "client_id": str(device_id),
            "local_id": str(uuid.uuid4()),
            "mode": "custom",
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "leak attempt"}],
        },
        headers=auth_headers,
    )
    assert bad.status_code == 400
    assert "Conversation not found in this project" in bad.json()["detail"]


def test_state_is_isolated(client, auth_headers):
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    client.put(
        f"/projects/{a['id']}/state",
        json={"patch": {"isolated_key": "secret_a"}},
        headers=auth_headers,
    )

    # Project B's state must still be None
    b_state = client.get(f"/projects/{b['id']}/state", headers=auth_headers).json()
    assert b_state is None


def test_handoffs_and_snapshots_are_isolated(client, auth_headers):
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    client.post(
        f"/projects/{a['id']}/handoffs",
        json={
            "to_role": "Implementation Agent",
            "from_role": "Architecture",
            "topic": "Handoff Topic A",
            "task": "Build A features",
        },
        headers=auth_headers,
    )

    b_handoffs = client.get(
        f"/projects/{b['id']}/handoffs", headers=auth_headers
    ).json()
    assert b_handoffs == []


def test_search_is_isolated(client, auth_headers):
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    unique_query = f"UniqueKeyword_{uuid.uuid4().hex}"
    client.post(
        f"/projects/{a['id']}/memory",
        json={
            "type": "fact",
            "title": f"Fact about {unique_query}",
            "content": f"Confidential details containing {unique_query}",
            "provenance": "user_confirmed",
        },
        headers=auth_headers,
    )

    # Search in A returns 1 match in memory
    res_a = client.get(f"/projects/{a['id']}/search?q={unique_query}", headers=auth_headers).json()
    assert len(res_a["memory"]) == 1

    # Search in B with same query MUST return 0 matches
    res_b = client.get(f"/projects/{b['id']}/search?q={unique_query}", headers=auth_headers).json()
    assert len(res_b["memory"]) == 0
    assert len(res_b["messages"]) == 0
    assert len(res_b["conversations"]) == 0
    assert len(res_b["handoffs"]) == 0


def test_export_is_isolated(client, auth_headers):
    a = _mk_project(client, auth_headers, f"A-{uuid.uuid4().hex[:6]}")
    b = _mk_project(client, auth_headers, f"B-{uuid.uuid4().hex[:6]}")

    unique_marker = f"Marker_{uuid.uuid4().hex}"
    client.post(
        f"/projects/{a['id']}/memory",
        json={
            "type": "decision",
            "title": f"Decision with {unique_marker}",
            "content": "Secret content",
            "provenance": "user_confirmed",
        },
        headers=auth_headers,
    )

    export_b = client.get(f"/projects/{b['id']}/export", headers=auth_headers).json()
    assert export_b["project"]["id"] == b["id"]
    assert export_b["memory_items"] == []
    # Assert marker does not appear in export JSON
    import json
    export_b_str = json.dumps(export_b)
    assert unique_marker not in export_b_str
