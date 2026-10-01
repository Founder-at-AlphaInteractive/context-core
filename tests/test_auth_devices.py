"""Tests for auth and device management endpoints."""

import uuid

import pytest
from sqlalchemy import select

from app.auth.security import generate_device_token, hash_device_token
from app.config import settings
from app.models.device import Device


def test_device_registration_success(client, db_session):
    resp = client.post(
        "/auth/device/register",
        json={
            "name": "Developer Laptop",
            "platform": "windows",
            "registration_secret": settings.device_registration_secret,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    raw_token = data["token"]
    device_id = uuid.UUID(data["device_id"])

    # Verify database state
    db_device = db_session.get(Device, device_id)
    assert db_device is not None
    assert db_device.name == "Developer Laptop"
    assert db_device.platform == "windows"
    # Database MUST store the hash, never the raw token!
    assert db_device.token_hash == hash_device_token(raw_token)
    assert raw_token not in db_device.token_hash
    assert db_device.revoked is False


def test_device_registration_invalid_secret(client):
    resp = client.post(
        "/auth/device/register",
        json={
            "name": "Hacker Device",
            "platform": "unknown",
            "registration_secret": "wrong-secret",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid device registration secret"


def test_read_current_device(client, db_session):
    raw_token = generate_device_token()
    device = Device(
        name="Current Phone",
        platform="android",
        token_hash=hash_device_token(raw_token),
        revoked=False,
    )
    db_session.add(device)
    db_session.commit()

    resp = client.get(
        "/auth/device/me",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(device.id)
    assert data["name"] == "Current Phone"
    assert data["platform"] == "android"


def test_list_and_revoke_devices(client, db_session):
    # Enrolled device 1 (caller)
    caller_token = generate_device_token()
    caller = Device(
        name="Caller Device",
        platform="macos",
        token_hash=hash_device_token(caller_token),
        revoked=False,
    )
    db_session.add(caller)

    # Enrolled device 2 (target)
    target_token = generate_device_token()
    target = Device(
        name="Old Phone",
        platform="ios",
        token_hash=hash_device_token(target_token),
        revoked=False,
    )
    db_session.add(target)
    db_session.commit()

    # List devices
    list_resp = client.get(
        "/devices",
        headers={"Authorization": f"Bearer {caller_token}"},
    )
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) >= 2
    device_ids = [it["id"] for it in items]
    assert str(caller.id) in device_ids
    assert str(target.id) in device_ids

    # Revoke target device
    revoke_resp = client.post(
        f"/devices/{target.id}/revoke",
        headers={"Authorization": f"Bearer {caller_token}"},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["revoked"] is True

    # Target device now fails authentication
    auth_resp = client.get(
        "/auth/device/me",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert auth_resp.status_code == 401


def test_revoke_nonexistent_device(client, db_session):
    caller_token = generate_device_token()
    caller = Device(
        name="Admin Device",
        platform="windows",
        token_hash=hash_device_token(caller_token),
        revoked=False,
    )
    db_session.add(caller)
    db_session.commit()

    missing_id = uuid.uuid4()
    resp = client.post(
        f"/devices/{missing_id}/revoke",
        headers={"Authorization": f"Bearer {caller_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device not found"
