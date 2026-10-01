import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.conversation import Conversation, Message
from app.models.device import Device
from app.models.enums import MemoryStatus, MemoryType, MessageRole, Provenance
from app.models.memory import MemoryItem
from app.models.project import Project, ProjectState
from app.services import combine as combine_service


@pytest.fixture
def auth_setup(db_session):
    project = Project(name="Combine Test Project", last_event_sequence=0)
    db_session.add(project)

    raw_token = generate_device_token()
    device = Device(
        name="Combine Device",
        platform="windows",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(device)
    return project, device, raw_token


def test_combine_empty_and_state_only_project(db_session, auth_setup):
    project, _, _ = auth_setup

    # 1. Completely empty project
    res_empty = combine_service.combine_project(
        db_session,
        project=project,
        task=None,
        target_role=None,
        include_recent_messages=0,
    )
    assert res_empty.project_id == project.id
    assert len(res_empty.active) == 0
    assert len(res_empty.completed) == 0
    assert len(res_empty.conflicts) == 0
    assert res_empty.suggested_next_ai_role == "Coding Architect"

    # 2. State-only project
    state = ProjectState(
        project_id=project.id,
        version=1,
        state_json={
            "phase": "Planning",
            "active": ["Draft schema"],
            "completed": ["Init repo"],
            "blocked": ["Awaiting budget approval"],
        },
    )
    db_session.add(state)
    db_session.commit()

    res_state = combine_service.combine_project(
        db_session,
        project=project,
        task=None,
        target_role=None,
        include_recent_messages=0,
    )
    assert res_state.active == ["Draft schema"]
    assert res_state.completed == ["Init repo"]
    assert res_state.blocked == ["Awaiting budget approval"]
    # Blocked item triggers Architecture role
    assert res_state.suggested_next_ai_role == "Architecture"


def test_combine_decisions_requirements_tasks_problems_conflicts(db_session, auth_setup):
    project, _, _ = auth_setup

    # Decision, Requirement, Task, Problem, and conflicting item
    d = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Postgres Selected",
        content="We chose Postgres.",
        key="db",
        value="Postgres",
    )
    r = MemoryItem(
        project_id=project.id,
        type=MemoryType.REQUIREMENT,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="REST API",
        content="Must support JSON REST endpoints.",
    )
    t = MemoryItem(
        project_id=project.id,
        type=MemoryType.TASK,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Setup Auth",
        content="Implement Bearer tokens.",
    )
    p = MemoryItem(
        project_id=project.id,
        type=MemoryType.PROBLEM,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="High Latency",
        content="Database query is slow.",
    )
    conflicting = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,
        title="MySQL Proposal",
        content="We should use MySQL instead.",
        key="db",
        value="MySQL",
    )
    db_session.add_all([d, r, t, p, conflicting])
    db_session.commit()

    res = combine_service.combine_project(
        db_session,
        project=project,
        task="Investigate performance",
        target_role=None,
        include_recent_messages=0,
    )
    # Decisions and requirements are reflected
    assert "Postgres Selected" in res.generated_prompt_md
    assert "REST API" in res.generated_prompt_md
    assert "Setup Auth" in res.generated_prompt_md
    assert "High Latency" in res.generated_prompt_md

    # Problems trigger Technical Advisor
    assert res.suggested_next_ai_role == "Technical Advisor"
    # Conflict detected between Postgres and MySQL
    assert len(res.conflicts) >= 1
    assert any(c.kind == "decision_contradiction" for c in res.conflicts)


def test_combine_target_role_precedence(db_session, auth_setup):
    project, _, _ = auth_setup

    # Problem exists (which would automatically suggest Technical Advisor)
    db_session.add(
        MemoryItem(
            project_id=project.id,
            type=MemoryType.PROBLEM,
            status=MemoryStatus.ACTIVE,
            provenance=Provenance.USER_CONFIRMED,
            title="Production bug",
            content="Critical error in auth",
        )
    )
    db_session.commit()

    # Caller explicitly designates target_role="Implementation Agent"
    res = combine_service.combine_project(
        db_session,
        project=project,
        task="Hotfix auth",
        target_role="Implementation Agent",
        include_recent_messages=0,
    )

    # Invariant: Caller-specified target_role has strict precedence
    assert res.suggested_next_ai_role == "Implementation Agent"
    assert "Caller explicitly designated target role" in res.suggested_next_ai_reason


def test_combine_include_recent_messages(db_session, auth_setup):
    project, _, _ = auth_setup

    conv = Conversation(project_id=project.id, cursor={})
    db_session.add(conv)
    db_session.flush()

    m = Message(
        project_id=project.id,
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="Can we review the deployment checklist?",
        content_hash="hash_deploy",
    )
    db_session.add(m)
    db_session.commit()

    res = combine_service.combine_project(
        db_session,
        project=project,
        task=None,
        target_role=None,
        include_recent_messages=10,
    )

    assert "Recent Activity Excerpt" in res.generated_prompt_md
    assert "deployment checklist" in res.generated_prompt_md


def test_combine_project_isolation(db_session, auth_setup):
    p1, _, _ = auth_setup
    p2 = Project(name="Project 2", last_event_sequence=0)
    db_session.add(p2)
    db_session.commit()

    db_session.add(
        MemoryItem(
            project_id=p2.id,
            type=MemoryType.DECISION,
            status=MemoryStatus.ACTIVE,
            provenance=Provenance.USER_CONFIRMED,
            title="P2 Confidential Decision",
            content="Secret content",
        )
    )
    db_session.commit()

    # Combine for P1 must never leak P2
    res_p1 = combine_service.combine_project(
        db_session,
        project=p1,
        task=None,
        target_role=None,
        include_recent_messages=0,
    )
    assert "P2 Confidential Decision" not in res_p1.generated_prompt_md
    assert "Secret content" not in res_p1.current_reality_md


def test_combine_api_route(client, auth_setup):
    project, _, token = auth_setup

    res = client.post(
        f"/projects/{project.id}/combine",
        json={
            "task": "Review architecture",
            "target_role": "Architecture",
            "include_recent_messages": 10,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == str(project.id)
    assert data["suggested_next_ai_role"] == "Architecture"
