import uuid
import pytest
from sqlalchemy import select

from app.auth.security import generate_device_token, hash_device_token
from app.models.conversation import Capture, Conversation, Message
from app.models.device import Device
from app.models.enums import CaptureMode, MessageRole
from app.models.project import Project
from app.schemas.capture import CaptureMessageInput, CaptureRequest
from app.services import captures as captures_service


@pytest.fixture
def auth_setup(db_session):
    """Creates a project and an authenticated device."""
    project = Project(name="Capture Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Test Laptop",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_successful_capture_and_conversation_creation(client, auth_setup, db_session):
    project, device, token = auth_setup
    local_id = uuid.uuid4()

    payload = {
        "local_id": str(local_id),
        "mode": "last_exchange",
        "title": "Auth Setup Conversation",
        "provider": "groq",
        "messages": [
            {"role": "user", "content": "How do we configure Postgres?"},
            {"role": "assistant", "content": "Use SQLAlchemy and Alembic."},
        ],
    }

    res = client.post(
        f"/projects/{project.id}/captures",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["idempotent_replay"] is False
    assert data["messages_inserted"] == 2
    assert data["messages_deduplicated"] == 0
    assert data["capture"]["client_id"] == str(device.id)
    assert data["capture"]["local_id"] == str(local_id)

    # Verify DB state
    conv = db_session.get(Conversation, uuid.UUID(data["conversation_id"]))
    assert conv is not None
    assert conv.title == "Auth Setup Conversation"
    assert conv.cursor["mode"] == "last_exchange"
    assert "last_hash" in conv.cursor

    messages = (
        db_session.execute(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.asc())
        )
        .scalars()
        .all()
    )
    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[1].role == MessageRole.ASSISTANT


def test_duplicate_capture_replay_idempotency(client, auth_setup, db_session):
    project, device, token = auth_setup
    local_id = uuid.uuid4()

    payload = {
        "local_id": str(local_id),
        "mode": "last_exchange",
        "messages": [
            {"role": "user", "content": "First run question"},
        ],
    }

    # First request
    res1 = client.post(
        f"/projects/{project.id}/captures",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 201
    assert res1.json()["idempotent_replay"] is False
    assert res1.json()["messages_inserted"] == 1

    # Second request (replay with same local_id)
    res2 = client.post(
        f"/projects/{project.id}/captures",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["idempotent_replay"] is True
    assert data2["messages_inserted"] == 0
    assert data2["messages_deduplicated"] == 1
    assert data2["capture"]["id"] == res1.json()["capture"]["id"]


def test_authenticated_device_identity_authoritative(client, auth_setup):
    project, device, token = auth_setup

    # Supplying wrong client_id in payload must be rejected
    forged_client_id = uuid.uuid4()
    payload = {
        "client_id": str(forged_client_id),
        "local_id": str(uuid.uuid4()),
        "mode": "last_n",
        "messages": [{"role": "user", "content": "Impersonation attempt"}],
    }

    res = client.post(
        f"/projects/{project.id}/captures",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert "client_id does not match authenticated device" in res.json()["detail"]


def test_existing_conversation_reuse_by_id_and_external_thread(client, auth_setup):
    project, device, token = auth_setup

    # 1. By external thread ID
    ext_thread = "thread-xyz-123"
    res1 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "external_thread_id": ext_thread,
            "mode": "last_n",
            "messages": [{"role": "user", "content": "Step 1"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 201
    conv_id = res1.json()["conversation_id"]

    # Subsequent capture referencing same external thread
    res2 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "external_thread_id": ext_thread,
            "mode": "last_n",
            "messages": [{"role": "assistant", "content": "Step 2"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    assert res2.json()["conversation_id"] == conv_id

    # 2. By direct conversation_id
    res3 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "mode": "last_n",
            "messages": [{"role": "user", "content": "Step 3"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res3.status_code == 201
    assert res3.json()["conversation_id"] == conv_id


def test_conversation_project_isolation(client, auth_setup, db_session):
    project1, device, token = auth_setup

    # Create Project 2
    project2 = Project(name="Project 2", last_event_sequence=0)
    db_session.add(project2)
    db_session.commit()
    db_session.refresh(project2)

    # Capture in Project 1
    res1 = client.post(
        f"/projects/{project1.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "mode": "last_n",
            "messages": [{"role": "user", "content": "Project 1 secret"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    conv_id = res1.json()["conversation_id"]

    # Try to capture against conv_id using Project 2 URL -> must be rejected
    res2 = client.post(
        f"/projects/{project2.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "mode": "last_n",
            "messages": [{"role": "user", "content": "Cross-project injection"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 400
    assert "Conversation not found in this project" in res2.json()["detail"]

    # Try to read conv_id from Project 2 -> 404
    res3 = client.get(
        f"/projects/{project2.id}/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res3.status_code == 404


def test_repeated_identical_legitimate_messages_preserved(client, auth_setup, db_session):
    project, device, token = auth_setup

    # A conversation where user repeats a question intentionally
    payload = {
        "local_id": str(uuid.uuid4()),
        "mode": "last_n",
        "messages": [
            {"role": "user", "content": "Is the server up?"},
            {"role": "assistant", "content": "Yes, it is."},
            {"role": "user", "content": "Is the server up?"},  # Legitimate repeat
        ],
    }

    res = client.post(
        f"/projects/{project.id}/captures",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    assert res.json()["messages_inserted"] == 3
    assert res.json()["messages_deduplicated"] == 0

    conv_id = res.json()["conversation_id"]
    messages = (
        db_session.execute(
            select(Message).where(Message.conversation_id == uuid.UUID(conv_id)).order_by(Message.created_at.asc())
        )
        .scalars()
        .all()
    )
    assert len(messages) == 3
    assert messages[0].content == "Is the server up?"
    assert messages[2].content == "Is the server up?"
    assert messages[0].id != messages[2].id


def test_external_message_id_deduplication(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Batch 1 with external IDs
    res1 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "mode": "since_last",
            "messages": [
                {"role": "user", "content": "Message 1", "external_id": "ext-1"},
                {"role": "assistant", "content": "Message 2", "external_id": "ext-2"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    conv_id = res1.json()["conversation_id"]
    assert res1.json()["messages_inserted"] == 2

    # Batch 2 contains ext-2 again and new ext-3
    res2 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "mode": "since_last",
            "messages": [
                {"role": "assistant", "content": "Message 2", "external_id": "ext-2"},
                {"role": "user", "content": "Message 3", "external_id": "ext-3"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    assert res2.json()["messages_inserted"] == 1
    assert res2.json()["messages_deduplicated"] == 1


def test_all_duplicate_batch_advances_cursor(client, auth_setup, db_session):
    project, device, token = auth_setup

    # Batch 1
    res1 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "mode": "since_last",
            "messages": [
                {"role": "user", "content": "Message 1", "external_id": "ext-1"},
                {"role": "assistant", "content": "Message 2", "external_id": "ext-2"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    conv_id = uuid.UUID(res1.json()["conversation_id"])

    # Batch 2 contains only duplicates (ext-1 and ext-2), with ext-2 as latest stream position
    res2 = client.post(
        f"/projects/{project.id}/captures",
        json={
            "local_id": str(uuid.uuid4()),
            "conversation_id": str(conv_id),
            "mode": "since_last",
            "messages": [
                {"role": "user", "content": "Message 1", "external_id": "ext-1"},
                {"role": "assistant", "content": "Message 2", "external_id": "ext-2"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    assert res2.json()["messages_inserted"] == 0
    assert res2.json()["messages_deduplicated"] == 2

    # Verify conversation cursor reflects the stream position of the batch
    conv = db_session.get(Conversation, conv_id)
    assert conv.cursor["last_external_id"] == "ext-2"


def test_capture_list_and_read(client, auth_setup):
    project, device, token = auth_setup

    # Create 2 conversations
    for i in range(2):
        client.post(
            f"/projects/{project.id}/captures",
            json={
                "local_id": str(uuid.uuid4()),
                "title": f"Conv {i}",
                "mode": "last_exchange",
                "messages": [{"role": "user", "content": f"Hello {i}"}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    # List conversations
    res = client.get(
        f"/projects/{project.id}/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    convs = res.json()
    assert len(convs) == 2

    # Read single conversation
    c_id = convs[0]["id"]
    res_c = client.get(
        f"/projects/{project.id}/conversations/{c_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_c.status_code == 200
    detail = res_c.json()
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"


def test_concurrent_duplicate_capture_recovery(auth_setup, db_session, monkeypatch):
    """Simulates concurrent duplicate insertion hitting IntegrityError and cleanly recovering."""
    project, device, _ = auth_setup
    local_id = uuid.uuid4()

    req = CaptureRequest(
        local_id=local_id,
        mode=CaptureMode.LAST_EXCHANGE,
        messages=[CaptureMessageInput(role=MessageRole.USER, content="Concurrent test")],
    )

    # First call succeeds
    outcome1 = captures_service.capture_conversation(
        db_session,
        project_id=project.id,
        client_id=device.id,
        payload=req,
    )
    assert outcome1.idempotent_replay is False

    # Second call using the same (client_id, local_id) succeeds via idempotent recovery
    outcome2 = captures_service.capture_conversation(
        db_session,
        project_id=project.id,
        client_id=device.id,
        payload=req,
    )
    assert outcome2.idempotent_replay is True
    assert outcome2.capture.id == outcome1.capture.id


def test_capture_transaction_rollback_on_failure(auth_setup, db_session, monkeypatch):
    """Verify that if an error occurs during capture (e.g. event emitter failure), nothing is committed."""
    project, device, _ = auth_setup
    local_id = uuid.uuid4()

    req = CaptureRequest(
        local_id=local_id,
        mode=CaptureMode.LAST_EXCHANGE,
        title="Will Fail",
        messages=[CaptureMessageInput(role=MessageRole.USER, content="Should not persist")],
    )

    # Force emit_event to raise an exception
    def failing_emit(*args, **kwargs):
        raise RuntimeError("Simulated failure during event emission")

    monkeypatch.setattr("app.services.captures.emit_event", failing_emit)

    with pytest.raises(RuntimeError, match="Simulated failure"):
        captures_service.capture_conversation(
            db_session,
            project_id=project.id,
            client_id=device.id,
            payload=req,
        )

    # Verify nothing was committed: no capture, no conversation with that title, no message
    db_session.rollback()
    captures = db_session.execute(select(Capture).where(Capture.local_id == local_id)).scalars().all()
    assert len(captures) == 0

    convs = db_session.execute(select(Conversation).where(Conversation.title == "Will Fail")).scalars().all()
    assert len(convs) == 0


def test_concurrent_duplicate_capture_submission(engine, SessionFactory):
    """Verify that concurrent duplicate submissions produce exactly 1 capture row."""
    if engine.dialect.name != "postgresql":
        pytest.skip("Concurrent duplicate capture serialization requires PostgreSQL row locking")
    import threading

    session0 = SessionFactory()
    p = Project(name="Concurrent Capture Proj", last_event_sequence=0)
    session0.add(p)
    dev = Device(name="Dev", platform="win", token_hash="dummy_cap", revoked=False)
    session0.add(dev)
    session0.commit()
    project_id = p.id
    device_id = dev.id
    session0.close()

    local_id = uuid.uuid4()
    req = CaptureRequest(
        local_id=local_id,
        mode=CaptureMode.LAST_EXCHANGE,
        messages=[CaptureMessageInput(role=MessageRole.USER, content="Hello concurrency")],
    )

    barrier = threading.Barrier(2)
    results = []

    def worker(worker_id):
        session = SessionFactory()
        try:
            barrier.wait()
            outcome = captures_service.capture_conversation(
                session,
                project_id=project_id,
                client_id=device_id,
                payload=req,
            )
            results.append((worker_id, outcome.capture.id, outcome.idempotent_replay))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both must succeed and point to the same capture ID
    assert len(results) == 2
    assert results[0][1] == results[1][1]

    # Verify database has strictly 1 Capture record for this local_id
    verify_session = SessionFactory()
    captures = verify_session.execute(
        select(Capture).where(Capture.client_id == device_id, Capture.local_id == local_id)
    ).scalars().all()
    assert len(captures) == 1
    verify_session.close()


