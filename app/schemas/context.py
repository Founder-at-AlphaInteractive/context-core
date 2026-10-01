import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContextRequest(BaseModel):
    role: str = Field(min_length=1, max_length=100)
    task: str | None = None
    token_budget: int | None = None
    persist_snapshot: bool = True
    conversation_id: uuid.UUID | None = None


class ContextSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    role: str
    task: str | None
    content_md: str
    token_count: int
    project_state_version: int | None
    created_at: datetime
