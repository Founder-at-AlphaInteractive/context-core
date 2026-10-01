"""Initial Context Core schema.

Revision ID: 0001_initial
Revises:
Create Date: 2024-12-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(values: list[str], name: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=64,
    )


AI_PROFILE_TYPE_VALUES = ["web", "api", "desktop", "vscode", "custom"]
CAPTURE_MODE_VALUES = ["last_exchange", "last_n", "since_last", "custom"]
MESSAGE_ROLE_VALUES = ["user", "assistant", "system"]
MEMORY_TYPE_VALUES = [
    "fact",
    "requirement",
    "decision",
    "proposal",
    "task",
    "problem",
]
MEMORY_STATUS_VALUES = [
    "proposed",
    "active",
    "superseded",
    "rejected",
    "completed",
    "archived",
]
PROVENANCE_VALUES = [
    "user_confirmed",
    "project_file",
    "test_result",
    "documentation",
    "confirmed_decision",
    "ai_extracted",
    "ai_proposal",
    "inference",
    "unknown",
]
JOB_STATUS_VALUES = [
    "pending",
    "running",
    "succeeded",
    "failed",
    "retrying",
    "dead",
]
EVENT_TYPE_VALUES = [
    "project_created",
    "project_updated",
    "ai_team_updated",
    "ai_profile_updated",
    "conversation_captured",
    "message_added",
    "memory_created",
    "memory_updated",
    "decision_created",
    "decision_updated",
    "task_created",
    "task_updated",
    "problem_created",
    "problem_updated",
    "project_state_changed",
    "handoff_created",
    "context_snapshot_created",
    "job_state_changed",
    "sync_required",
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "last_event_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_projects_name", "projects", ["name"])

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column(
            "revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_devices_token_hash", "devices", ["token_hash"])

    # ------------------------------------------------------------------
    # ai_profiles
    # ------------------------------------------------------------------
    op.create_table(
        "ai_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column(
            "type",
            _enum(AI_PROFILE_TYPE_VALUES, "ai_profile_type"),
            nullable=False,
        ),
        sa.Column(
            "detection_hints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ------------------------------------------------------------------
    # project_ai_roles
    # ------------------------------------------------------------------
    op.create_table(
        "project_ai_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ai_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_name", sa.String(length=100), nullable=False),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id", "ai_profile_id", "role_name", name="uq_project_ai_role"
        ),
    )
    op.create_index(
        "ix_project_ai_roles_project_id", "project_ai_roles", ["project_id"]
    )
    op.create_index(
        "ix_project_ai_roles_ai_profile_id", "project_ai_roles", ["ai_profile_id"]
    )

    # ------------------------------------------------------------------
    # conversations
    # ------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ai_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "project_ai_role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_ai_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("external_thread_id", sa.String(length=300), nullable=True),
        sa.Column(
            "cursor",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    op.create_index(
        "ix_conversations_external_thread_id",
        "conversations",
        ["external_thread_id"],
    )

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", _enum(MESSAGE_ROLE_VALUES, "message_role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "conversation_id", "content_hash", name="uq_message_conv_hash"
        ),
    )
    op.create_index("ix_messages_project_id", "messages", ["project_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_external_id", "messages", ["external_id"])

    # ------------------------------------------------------------------
    # captures
    # ------------------------------------------------------------------
    op.create_table(
        "captures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", _enum(CAPTURE_MODE_VALUES, "capture_mode"), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="stored"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("client_id", "local_id", name="uq_capture_idempotency"),
    )
    op.create_index("ix_captures_project_id", "captures", ["project_id"])
    op.create_index("ix_captures_conversation_id", "captures", ["conversation_id"])

    # ------------------------------------------------------------------
    # memory_items
    # ------------------------------------------------------------------
    op.create_table(
        "memory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", _enum(MEMORY_TYPE_VALUES, "memory_type"), nullable=False),
        sa.Column(
            "status",
            _enum(MEMORY_STATUS_VALUES, "memory_status"),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column(
            "provenance",
            _enum(PROVENANCE_VALUES, "memory_provenance"),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_capture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("captures.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_memory_items_project_id", "memory_items", ["project_id"])
    op.create_index(
        "ix_memory_project_type_status",
        "memory_items",
        ["project_id", "type", "status"],
    )
    op.create_index(
        "ix_memory_project_key", "memory_items", ["project_id", "key"]
    )

    # ------------------------------------------------------------------
    # project_states
    # ------------------------------------------------------------------
    op.create_table(
        "project_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "state_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_by_device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_project_state_version"
        ),
    )
    op.create_index("ix_project_states_project_id", "project_states", ["project_id"])

    # ------------------------------------------------------------------
    # context_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("project_state_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_context_snapshots_project_id", "context_snapshots", ["project_id"]
    )

    # ------------------------------------------------------------------
    # handoffs
    # ------------------------------------------------------------------
    op.create_table(
        "handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_ai_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_role", sa.String(length=100), nullable=True),
        sa.Column(
            "to_ai_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("to_role", sa.String(length=100), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "context_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("context_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_handoffs_project_id", "handoffs", ["project_id"])

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("type", _enum(EVENT_TYPE_VALUES, "event_type"), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "sequence", name="uq_event_project_seq"),
    )
    op.create_index("ix_events_project_id", "events", ["project_id"])
    op.create_index("ix_events_sequence", "events", ["sequence"])

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            _enum(JOB_STATUS_VALUES, "job_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])

    # ------------------------------------------------------------------
    # idempotency_keys
    # ------------------------------------------------------------------
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column(
            "response_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_type", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_events_sequence", table_name="events")
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_handoffs_project_id", table_name="handoffs")
    op.drop_table("handoffs")
    op.drop_index("ix_context_snapshots_project_id", table_name="context_snapshots")
    op.drop_table("context_snapshots")
    op.drop_index("ix_project_states_project_id", table_name="project_states")
    op.drop_table("project_states")
    op.drop_index("ix_memory_project_key", table_name="memory_items")
    op.drop_index("ix_memory_project_type_status", table_name="memory_items")
    op.drop_index("ix_memory_items_project_id", table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_index("ix_captures_conversation_id", table_name="captures")
    op.drop_index("ix_captures_project_id", table_name="captures")
    op.drop_table("captures")
    op.drop_index("ix_messages_external_id", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_index("ix_messages_project_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_external_thread_id", table_name="conversations")
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_project_ai_roles_ai_profile_id", table_name="project_ai_roles")
    op.drop_index("ix_project_ai_roles_project_id", table_name="project_ai_roles")
    op.drop_table("project_ai_roles")
    op.drop_table("ai_profiles")
    op.drop_index("ix_devices_token_hash", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_table("projects")
