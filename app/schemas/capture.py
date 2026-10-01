import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CaptureMode, MessageRole


class CaptureMessageInput(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1)
    external_id: str | None = None
    content_hash: str | None = None
    captured_at: datetime | None = None


class CaptureRequest(BaseModel):
    """Idempotent capture request from a client.

    client_id + local_id must be stable across retries.
    """

    client_id: uuid.UUID | None = None
    local_id: uuid.UUID
    mode: CaptureMode

    # Either reuse an existing conversation or provide an external thread id
    # so the server can find/create one.
    conversation_id: uuid.UUID | None = None
    external_thread_id: str | None = None
    provider: str | None = None

    ai_profile_id: uuid.UUID | None = None
    project_ai_role_id: uuid.UUID | None = None
    title: str | None = None

    messages: list[CaptureMessageInput] = Field(default_factory=list)


class CaptureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID
    client_id: uuid.UUID
    local_id: uuid.UUID
    mode: CaptureMode
    message_count: int
    status: str
    created_at: datetime


class CaptureResult(BaseModel):
    capture: CaptureRead
    conversation_id: uuid.UUID
    messages_inserted: int
    messages_deduplicated: int
    idempotent_replay: bool
