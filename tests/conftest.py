"""Pytest fixtures.

Supports a running PostgreSQL instance when configured via TEST_DATABASE_URL or DATABASE_URL:
    TEST_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db

If PostgreSQL is unreachable in the local environment, falls back to an isolated
in-memory engine so unit tests can execute deterministically without external daemons.
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Environment MUST be set before importing app modules (Settings is cached).
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://context_core:context_core@localhost:5432/context_core_test",
    ),
)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-do-not-use-in-production-1234")
os.environ.setdefault("DEVICE_REGISTRATION_SECRET", "test-registration-secret")
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("WORKER_POLL_INTERVAL_SECONDS", "3600")

from app.config import settings
from app.db.base import Base
import app.models  # noqa: F401 (register all ORM models)
from app.db.session import get_db
from app.main import app as fastapi_app

# Disable background worker during test execution
settings.worker_enabled = False


# SQLite dialect compatibility compilers for offline unit testing
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


def _probe_postgres(url: str):
    try:
        eng = create_engine(url, pool_pre_ping=True, future=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception:
        return None


@pytest.fixture(scope="session")
def engine():
    test_url = os.environ.get("TEST_DATABASE_URL", settings.database_url)
    eng = _probe_postgres(test_url)
    if eng is not None:
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
        yield eng
        Base.metadata.drop_all(eng)
        eng.dispose()
    else:
        # Fallback to in-memory SQLite engine
        fallback_eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(fallback_eng)
        yield fallback_eng
        Base.metadata.drop_all(fallback_eng)
        fallback_eng.dispose()


@pytest.fixture(scope="session")
def SessionFactory(engine):
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture
def db_session(engine):
    """Provides an isolated database session per test with clean tables."""
    # Ensure fresh schema for isolation
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    session: Session = SessionMaker()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def db(db_session):
    """Alias for db_session fixture."""
    return db_session


@pytest.fixture
def client(db_session):
    fastapi_app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def device_token(client):
    """Register a fresh device and return (device_id, raw_token)."""
    resp = client.post(
        "/auth/device/register",
        json={
            "name": f"test-device-{uuid.uuid4().hex[:6]}",
            "platform": "test",
            "registration_secret": "test-registration-secret",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["device_id"], data["token"]


@pytest.fixture
def auth_headers(device_token):
    _, token = device_token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def project(client, auth_headers):
    resp = client.post(
        "/projects",
        json={"name": f"Test Project {uuid.uuid4().hex[:6]}", "description": "test"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
