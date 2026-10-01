import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AIProfileType


class AIProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    type: AIProfileType
    detection_hints: dict = Field(default_factory=dict)
    permissions: dict = Field(default_factory=dict)


class AIProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider: str
    type: AIProfileType
    detection_hints: dict
    permissions: dict
    created_at: datetime
    updated_at: datetime


class ProjectAIRoleCreate(BaseModel):
    ai_profile_id: uuid.UUID
    role_name: str = Field(min_length=1, max_length=100)
    permissions: dict = Field(default_factory=dict)


class ProjectAIRoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    ai_profile_id: uuid.UUID
    role_name: str
    permissions: dict
    created_at: datetime


class ProjectAIRoleWithProfile(ProjectAIRoleRead):
    ai_profile: AIProfileRead
