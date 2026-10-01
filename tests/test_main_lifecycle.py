"""Tests for main application lifecycle, middleware, and healthz endpoint."""

import asyncio
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth.security import generate_device_token, hash_device_token
from app.config import settings
from app.main import app
from app.models.device import Device
from app.workers.poller import run_poller


def test_healthz_endpoint(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["environment"] == settings.environment
    assert "groq_configured" in data
    # Ensure no secrets or API keys are exposed
    assert "groq_api_key" not in data
    assert "jwt_secret" not in data
    assert "device_registration_secret" not in data


def test_request_id_middleware(client):
    resp = client.get("/healthz")
    assert "X-Request-ID" in resp.headers
    req_id = resp.headers["X-Request-ID"]
    # Must be valid UUID
    parsed = uuid.UUID(req_id)
    assert str(parsed) == req_id


def test_unhandled_exception_sanitized_500(client):
    @app.get("/test-internal-crash")
    def crash_route():
        raise RuntimeError("Internal critical failure: DB_SECRET_XYZ")

    resp = client.get("/test-internal-crash")
    assert resp.status_code == 500
    assert "X-Request-ID" in resp.headers
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert "request_id" in body
    # Traceback and internal message must NOT leak to client
    assert "DB_SECRET_XYZ" not in resp.text


@pytest.mark.asyncio
async def test_poller_clean_startup_and_shutdown(db_session):
    stop_event = asyncio.Event()

    from contextlib import contextmanager

    @contextmanager
    def mock_factory():
        yield db_session

    # Start poller task with test database session factory
    task = asyncio.create_task(run_poller(stop_event, session_factory=mock_factory))

    # Give it a brief moment to log poller_started
    await asyncio.sleep(0.05)

    # Signal stop
    stop_event.set()

    # Wait for clean completion
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


def test_settings_without_jwt_secret(monkeypatch):
    """Verify Settings instantiates cleanly without requiring JWT_SECRET."""
    from app.config import Settings
    monkeypatch.delenv("JWT_SECRET", raising=False)
    s = Settings(_env_file=None, jwt_secret=None)
    assert s.jwt_secret is None


def test_settings_worker_enabled_toggle():
    """Verify worker_enabled flag can be configured."""
    from app.config import Settings
    s_default = Settings(_env_file=None)
    assert s_default.worker_enabled is True

    s_disabled = Settings(_env_file=None, worker_enabled=False)
    assert s_disabled.worker_enabled is False

