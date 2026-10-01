import uuid
import pytest

from app.auth.security import generate_device_token, hash_device_token
from app.models.ai_profile import AIProfile
from app.models.device import Device
from app.models.enums import AIProfileType, EventType
from app.models.event import Event
from app.models.project import Project
from app.services import ai_team as ai_team_service
from app.services import projects as projects_service


def test_ai_profile_crud_and_conflict(db_session):
    profile = ai_team_service.create_ai_profile(
        db_session,
        name="ChatGPT Architect",
        provider="openai",
        type=AIProfileType.WEB,
        detection_hints={"domain": "chatgpt.com"},
        permissions={"read": True},
    )
    assert profile.id is not None
    assert profile.name == "ChatGPT Architect"

    # Duplicate name should raise conflict without killing the session
    with pytest.raises(ai_team_service.AIProfileNameConflict):
        ai_team_service.create_ai_profile(
            db_session,
            name="ChatGPT Architect",
            provider="openai",
            type=AIProfileType.WEB,
        )

    # Session is still healthy!
    all_profiles = ai_team_service.list_ai_profiles(db_session)
    assert len(all_profiles) == 1


def test_project_ai_role_assignment_and_isolation(db_session):
    p1 = projects_service.create_project(db_session, name="Project 1")
    p2 = projects_service.create_project(db_session, name="Project 2")

    profile = ai_team_service.create_ai_profile(
        db_session,
        name="DeepSeek Coder",
        provider="deepseek",
        type=AIProfileType.API,
    )

    # Assign role to Project 1
    role1 = ai_team_service.assign_role(
        db_session,
        project_id=p1.id,
        ai_profile_id=profile.id,
        role_name="Lead Coder",
        permissions={"write": True},
    )
    assert role1.id is not None
    assert role1.project_id == p1.id

    # Duplicate role in Project 1 raises ProjectRoleConflict
    with pytest.raises(ai_team_service.ProjectRoleConflict):
        ai_team_service.assign_role(
            db_session,
            project_id=p1.id,
            ai_profile_id=profile.id,
            role_name="Lead Coder",
        )

    # Same role in Project 2 is ALLOWED (project scoped)
    role2 = ai_team_service.assign_role(
        db_session,
        project_id=p2.id,
        ai_profile_id=profile.id,
        role_name="Lead Coder",
    )
    assert role2.id is not None
    assert role2.project_id == p2.id

    # Eager listing prevents N+1 and returns enriched profile
    roles_p1 = ai_team_service.list_roles(db_session, p1.id)
    assert len(roles_p1) == 1
    assert roles_p1[0].ai_profile.name == "DeepSeek Coder"


def test_ai_team_api_flow(client, db_session):
    token = generate_device_token()
    device = Device(
        name="Team CLI",
        platform="macos",
        token_hash=hash_device_token(token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create AI profile via API
    resp = client.post(
        "/ai-profiles",
        json={
            "name": "Qwen Architect",
            "provider": "alibaba",
            "type": "api",
            "detection_hints": {},
            "permissions": {},
        },
        headers=headers,
    )
    assert resp.status_code == 201
    profile_id = resp.json()["id"]

    # Duplicate profile name returns 409
    resp = client.post(
        "/ai-profiles",
        json={
            "name": "Qwen Architect",
            "provider": "alibaba",
            "type": "api",
        },
        headers=headers,
    )
    assert resp.status_code == 409

    # 2. Create projects
    p1 = projects_service.create_project(db_session, name="Team Proj 1")
    p2 = projects_service.create_project(db_session, name="Team Proj 2")

    # 3. Assign role
    resp = client.post(
        f"/projects/{p1.id}/ai-team",
        json={
            "ai_profile_id": profile_id,
            "role_name": "System Architect",
            "permissions": {"full_access": True},
        },
        headers=headers,
    )
    assert resp.status_code == 201
    role_data = resp.json()
    role_id = role_data["id"]
    assert role_data["ai_profile"]["name"] == "Qwen Architect"

    # 4. List team
    resp = client.get(f"/projects/{p1.id}/ai-team", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 5. Cross-project role deletion attempt (trying to delete p1's role via p2 endpoint)
    resp = client.delete(f"/projects/{p2.id}/ai-team/{role_id}", headers=headers)
    assert resp.status_code == 404  # Rejected!

    # 6. Correct deletion
    resp = client.delete(f"/projects/{p1.id}/ai-team/{role_id}", headers=headers)
    assert resp.status_code == 204

    # Verify team is now empty
    resp = client.get(f"/projects/{p1.id}/ai-team", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 0
