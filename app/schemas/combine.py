import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.memory import ConflictRead


class CombineRequest(BaseModel):
    task: str | None = None
    target_role: str | None = None
    include_recent_messages: int = Field(default=20, ge=0, le=200)


class CombineNextStep(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    description: str
    recommended_role: str | None = None
    reason: str | None = None


class CombineResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    generated_at: datetime
    current_reality_md: str
    changes_md: str
    completed: list[str]
    active: list[str]
    blocked: list[str]
    conflicts: list[ConflictRead]
    open_questions: list[str]
    recommended_next_step: CombineNextStep | None
    suggested_next_ai_role: str | None
    suggested_next_ai_reason: str | None
    generated_prompt_md: str
