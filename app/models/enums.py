import enum
from sqlalchemy import Enum as SAEnum


class MemoryType(str, enum.Enum):
    FACT = "fact"
    REQUIREMENT = "requirement"
    DECISION = "decision"
    PROPOSAL = "proposal"
    TASK = "task"
    PROBLEM = "problem"


class MemoryStatus(str, enum.Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Provenance(str, enum.Enum):
    USER_CONFIRMED = "user_confirmed"
    PROJECT_FILE = "project_file"
    TEST_RESULT = "test_result"
    DOCUMENTATION = "documentation"
    CONFIRMED_DECISION = "confirmed_decision"
    AI_EXTRACTED = "ai_extracted"
    AI_PROPOSAL = "ai_proposal"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class AIProfileType(str, enum.Enum):
    WEB = "web"
    API = "api"
    DESKTOP = "desktop"
    VSCODE = "vscode"
    CUSTOM = "custom"


class CaptureMode(str, enum.Enum):
    LAST_EXCHANGE = "last_exchange"
    LAST_N = "last_n"
    SINCE_LAST = "since_last"
    CUSTOM = "custom"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"


class EventType(str, enum.Enum):
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    AI_TEAM_UPDATED = "ai_team_updated"
    AI_PROFILE_UPDATED = "ai_profile_updated"
    CONVERSATION_CAPTURED = "conversation_captured"
    MESSAGE_ADDED = "message_added"
    MEMORY_CREATED = "memory_created"
    MEMORY_UPDATED = "memory_updated"
    DECISION_CREATED = "decision_created"
    DECISION_UPDATED = "decision_updated"
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    PROBLEM_CREATED = "problem_created"
    PROBLEM_UPDATED = "problem_updated"
    PROJECT_STATE_CHANGED = "project_state_changed"
    HANDOFF_CREATED = "handoff_created"
    CONTEXT_SNAPSHOT_CREATED = "context_snapshot_created"
    JOB_STATE_CHANGED = "job_state_changed"
    SYNC_REQUIRED = "sync_required"


# Provenance ranking used to decide whether new info may override existing info.
PROVENANCE_RANK: dict[Provenance, int] = {
    Provenance.USER_CONFIRMED: 100,
    Provenance.CONFIRMED_DECISION: 95,
    Provenance.TEST_RESULT: 90,
    Provenance.PROJECT_FILE: 85,
    Provenance.DOCUMENTATION: 70,
    Provenance.AI_EXTRACTED: 50,
    Provenance.AI_PROPOSAL: 40,
    Provenance.INFERENCE: 20,
    Provenance.UNKNOWN: 0,
}


def sa_enum(py_enum, name: str, length: int = 64) -> SAEnum:
    """Helper to create non-native enum with explicit CHECK constraint."""
    return SAEnum(
        py_enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda e: [x.value for x in e],
        length=length,
        validate_strings=True,
    )
