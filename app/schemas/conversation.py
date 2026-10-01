import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import MessageRole


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    external_id: str | None
    content_hash: str
    captured_at: datetime | None
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    ai_profile_id: uuid.UUID | None
    project_ai_role_id: uuid.UUID | None
    title: str | None
    provider: str | None
    external_thread_id: str | None
    cursor: dict
    created_at: datetime
    updated_at: datetime


class ConversationWithMessages(ConversationRead):
    messages: list[MessageRead]
