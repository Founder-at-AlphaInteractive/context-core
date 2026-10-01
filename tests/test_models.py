import uuid
from app.db.base import Base
from app.models import (
    AIProfile,
    AIProfileType,
    Capture,
    CaptureMode,
    ContextSnapshot,
    Conversation,
    Device,
    Event,
    EventType,
    Handoff,
    IdempotencyKey,
    Job,
    JobStatus,
    MemoryItem,
    MemoryStatus,
    MemoryType,
    Message,
    MessageRole,
    Project,
    ProjectAIRole,
    ProjectState,
    Provenance,
    PROVENANCE_RANK,
)


def test_metadata_tables_registered():
    """Verify that all 14 expected models are properly registered on Base.metadata."""
    expected_tables = {
        "projects",
        "devices",
        "ai_profiles",
        "project_ai_roles",
        "conversations",
        "messages",
        "captures",
        "memory_items",
        "project_states",
        "context_snapshots",
        "handoffs",
        "events",
        "jobs",
        "idempotency_keys",
    }
    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(registered_tables), (
        f"Missing tables: {expected_tables - registered_tables}"
    )


def test_provenance_ranks():
    """Verify provenance ranking invariant: user confirmed > AI extracted > AI proposal."""
    assert PROVENANCE_RANK[Provenance.USER_CONFIRMED] > PROVENANCE_RANK[Provenance.AI_EXTRACTED]
    assert PROVENANCE_RANK[Provenance.AI_EXTRACTED] > PROVENANCE_RANK[Provenance.AI_PROPOSAL]
    assert PROVENANCE_RANK[Provenance.AI_PROPOSAL] > PROVENANCE_RANK[Provenance.UNKNOWN]


def test_model_instantiation():
    """Verify basic instantiation of models without DB errors."""
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="Test Project",
        description="A test project",
        last_event_sequence=0,
    )
    assert project.id == project_id
    assert project.name == "Test Project"

    mem = MemoryItem(
        project_id=project.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Engine Selection",
        content="We chose Unity",
        key="engine",
        value="Unity",
    )
    assert mem.key == "engine"
    assert mem.value == "Unity"
    assert mem.provenance == Provenance.USER_CONFIRMED


def test_alembic_migration_offline():
    """Verify that Alembic initial migration compiles offline to valid SQL."""
    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Alembic migration failed: {res.stderr}"
    assert "CREATE TABLE projects" in res.stdout
    assert "CONSTRAINT memory_provenance CHECK" in res.stdout
    assert "CONSTRAINT uq_event_project_seq UNIQUE" in res.stdout

