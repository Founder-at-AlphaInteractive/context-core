import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.security import (
    generate_device_token,
    hash_device_token,
    verify_device_registration_secret,
)
from app.api.deps import _extract_bearer, get_current_device, get_project
from app.db.base import Base
from app.models.device import Device
from app.models.project import Project




def test_token_generation_and_hashing():
    token1 = generate_device_token()
    token2 = generate_device_token()
    assert token1 != token2
    assert len(token1) >= 48

    hash1 = hash_device_token(token1)
    hash2 = hash_device_token(token1)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex string


def test_verify_registration_secret(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "device_registration_secret", "secret-test-key-12345")

    assert verify_device_registration_secret("secret-test-key-12345") is True
    assert verify_device_registration_secret("wrong-secret") is False
    assert verify_device_registration_secret("") is False


def test_extract_bearer_header():
    # Missing
    with pytest.raises(HTTPException) as exc:
        _extract_bearer(None)
    assert exc.value.status_code == 401

    # Wrong scheme
    with pytest.raises(HTTPException) as exc:
        _extract_bearer("Basic xyz123")
    assert exc.value.status_code == 401

    # Malformed / extra parts
    with pytest.raises(HTTPException) as exc:
        _extract_bearer("Bearer token extra")
    assert exc.value.status_code == 401

    # Empty token
    with pytest.raises(HTTPException) as exc:
        _extract_bearer("Bearer   ")
    assert exc.value.status_code == 401

    # Valid
    assert _extract_bearer("Bearer my-valid-token") == "my-valid-token"
    assert _extract_bearer("bearer my-valid-token") == "my-valid-token"


def test_get_current_device_auth_flow(db_session):
    raw_token = generate_device_token()
    token_hash = hash_device_token(raw_token)

    device = Device(
        name="Laptop CLI",
        platform="windows",
        token_hash=token_hash,
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()

    # Valid token
    auth_header = f"Bearer {raw_token}"
    authed_device = get_current_device(authorization=auth_header, db=db_session)
    assert authed_device.id == device.id
    assert authed_device.last_seen_at is not None

    # Invalid token
    with pytest.raises(HTTPException) as exc:
        get_current_device(authorization="Bearer wrong-token", db=db_session)
    assert exc.value.status_code == 401

    # Revoked token
    device.revoked = True
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        get_current_device(authorization=auth_header, db=db_session)
    assert exc.value.status_code == 401


def test_get_project_lookup(db_session):
    raw_token = generate_device_token()
    device = Device(
        name="Test Device",
        platform="linux",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    project = Project(
        name="Test Project",
        description="Sample",
        last_event_sequence=0,
    )
    db_session.add_all([device, project])
    db_session.commit()

    # Existing project
    res = get_project(project_id=project.id, db=db_session, device=device)
    assert res.id == project.id

    # Non-existent project
    random_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        get_project(project_id=random_id, db=db_session, device=device)
    assert exc.value.status_code == 404
