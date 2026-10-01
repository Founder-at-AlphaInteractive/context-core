import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.conversation import Conversation, Message
from app.models.device import Device
from app.models.enums import MemoryStatus, MemoryType, MessageRole, Provenance
from app.models.memory import MemoryItem
from app.models.project import Project, ProjectState
from app.schemas.context import ContextRequest
from app.services import context_compiler


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Context Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Context Test Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_compiler_budget_default_and_explicit(db_session, auth_setup):
    project, _, _ = auth_setup

    # Default budget
    c1 = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="System design",
        token_budget=None,
    )
    assert c1.token_count > 0
    assert c1.token_count <= 2000

    # Explicit budget
    c2 = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="System design",
        token_budget=300,
    )
    assert c2.token_count <= 300


def test_compiler_invalid_and_zero_budget_rejected(db_session, auth_setup):
    project, _, _ = auth_setup

    with pytest.raises(ValueError, match="greater than zero"):
        context_compiler.compile_context(
            db_session,
            project=project,
            role="architecture",
            task=None,
            token_budget=0,
        )

    with pytest.raises(ValueError, match="between 50 and 100000"):
        context_compiler.compile_context(
            db_session,
            project=project,
            role="architecture",
            task=None,
            token_budget=20,  # Below min 50
        )


def test_compiler_hard_budget_invariant_with_large_items(db_session, auth_setup):
    project, _, _ = auth_setup

    # Create multiple large memory items
    for i in range(20):
        db_session.add(
            MemoryItem(
                project_id=project.id,
                type=MemoryType.DECISION,
                status=MemoryStatus.ACTIVE,
                provenance=Provenance.USER_CONFIRMED,
                title=f"Large Architectural Decision {i}",
                content="Long technical specification details. " * 30,
            )
        )
    db_session.commit()

    tight_budget = 450
    compiled = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="Check budget",
        token_budget=tight_budget,
    )

    # Invariant: compiled.token_count must strictly satisfy the hard budget
    assert compiled.token_count <= tight_budget
    assert len(compiled.included_memory_ids) > 0
    # Exactly matches tracked ids in output
    assert len(compiled.included_memory_ids) == len(set(compiled.included_memory_ids))


def test_compiler_authority_provenance_markers(db_session, auth_setup):
    project, _, _ = auth_setup

    user_confirmed_item = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Authoritative Engine Choice",
        content="PostgreSQL selected.",
    )
    ai_proposal_item = MemoryItem(
        project_id=project.id,
        type=MemoryType.PROPOSAL,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,
        title="Proposed Cache Choice",
        content="Consider Redis.",
    )
    db_session.add_all([user_confirmed_item, ai_proposal_item])
    db_session.commit()

    compiled = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="Review stack",
        token_budget=1000,
    )

    # Must distinguish between authoritative and unapproved proposal
    assert "[USER_CONFIRMED | ACTIVE]" in compiled.content_md
    assert "[AI_PROPOSAL | PROPOSED (Unapproved)]" in compiled.content_md


def test_compiler_role_specific_filtering_and_sections(db_session, auth_setup):
    project, _, _ = auth_setup

    # Task item (implementation) vs Problem item (technical advisor)
    task_item = MemoryItem(
        project_id=project.id,
        type=MemoryType.TASK,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Build user table",
        content="Execute DDL",
    )
    problem_item = MemoryItem(
        project_id=project.id,
        type=MemoryType.PROBLEM,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="DB connection pool leak",
        content="Fix pool overflow",
    )
    db_session.add_all([task_item, problem_item])
    db_session.commit()

    # Technical advisor focuses on problems and excludes tasks
    advisor_ctx = context_compiler.compile_context(
        db_session,
        project=project,
        role="technical_advisor",
        task="Diagnose pool",
        token_budget=1000,
    )
    assert problem_item.id in advisor_ctx.included_memory_ids
    assert task_item.id not in advisor_ctx.included_memory_ids

    # Implementation agent focuses on tasks and excludes problems
    agent_ctx = context_compiler.compile_context(
        db_session,
        project=project,
        role="implementation_agent",
        task="Build tables",
        token_budget=1000,
    )
    assert task_item.id in agent_ctx.included_memory_ids
    assert problem_item.id not in agent_ctx.included_memory_ids


def test_compiler_tolerates_malformed_state_json(db_session, auth_setup):
    project, _, _ = auth_setup

    # State with non-list completed, integer objective, etc.
    state = ProjectState(
        project_id=project.id,
        version=1,
        state_json={
            "objective": 12345,  # Non-string objective
            "completed": "Single completed task as string",  # Not a list
            "active": {"task1": "in progress"},  # Dict instead of list
            "blocked": None,
        },
    )
    db_session.add(state)
    db_session.commit()

    compiled = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="Test state tolerance",
        token_budget=1000,
    )
    assert "12345" in compiled.content_md
    assert "Single completed task as string" in compiled.content_md
    assert "task1: in progress" in compiled.content_md


def test_compiler_recent_message_scope(db_session, auth_setup):
    project, _, _ = auth_setup

    conv1 = Conversation(project_id=project.id, cursor={})
    conv2 = Conversation(project_id=project.id, cursor={})
    db_session.add_all([conv1, conv2])
    db_session.flush()

    m1 = Message(
        project_id=project.id,
        conversation_id=conv1.id,
        role=MessageRole.USER,
        content="Secret message from Conv1",
        content_hash="hash1",
    )
    m2 = Message(
        project_id=project.id,
        conversation_id=conv2.id,
        role=MessageRole.USER,
        content="Message from Conv2",
        content_hash="hash2",
    )
    db_session.add_all([m1, m2])
    db_session.commit()

    # Scoped to conv1 only
    compiled = context_compiler.compile_context(
        db_session,
        project=project,
        role="architecture",
        task="Scoped recent",
        token_budget=1000,
        conversation_id=conv1.id,
    )
    assert "Secret message from Conv1" in compiled.content_md
    assert "Message from Conv2" not in compiled.content_md


def test_compiler_api_flow(client, auth_setup):
    project, _, token = auth_setup

    # 1. Ephemeral generation
    res1 = client.post(
        f"/projects/{project.id}/context",
        json={
            "role": "architecture",
            "task": "API flow test",
            "token_budget": 500,
            "persist_snapshot": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["token_count"] <= 500
    assert data1["role"] == "architecture"

    # 2. Persisted snapshot
    res2 = client.post(
        f"/projects/{project.id}/context",
        json={
            "role": "coding_architect",
            "task": "Persisted snapshot test",
            "token_budget": 600,
            "persist_snapshot": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["token_count"] <= 600

    # 3. Invalid budget
    res3 = client.post(
        f"/projects/{project.id}/context",
        json={
            "role": "architecture",
            "token_budget": 0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res3.status_code == 400
    assert "token_budget must be greater than zero" in res3.json()["detail"]
