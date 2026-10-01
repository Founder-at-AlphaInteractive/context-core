import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HandoffRequest(BaseModel):
    to_role: str = Field(min_length=1, max_length=100)
    to_ai_profile_id: uuid.UUID | None = None
    from_role: str | None = Field(default=None, max_length=100)
    from_ai_profile_id: uuid.UUID | None = None
    topic: str = Field(min_length=1, max_length=300)
    task: str | None = None
    token_budget: int | None = Field(default=None, ge=200, le=32000)


class HandoffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    from_ai_profile_id: uuid.UUID | None
    from_role: str | None
    to_ai_profile_id: uuid.UUID | None
    to_role: str
    topic: str
    content_md: str
    context_snapshot_id: uuid.UUID | None
    created_at: datetime
