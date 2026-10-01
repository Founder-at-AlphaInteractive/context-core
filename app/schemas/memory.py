import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MemoryStatus, MemoryType, Provenance


class MemoryItemCreate(BaseModel):
    type: MemoryType
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    status: MemoryStatus | None = None
    provenance: Provenance = Provenance.USER_CONFIRMED
    key: str | None = Field(default=None, max_length=200)
    value: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    source_capture_id: uuid.UUID | None = None
    source_message_id: uuid.UUID | None = None
    supersedes_id: uuid.UUID | None = None


class MemoryItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1)
    status: MemoryStatus | None = None
    provenance: Provenance | None = None
    key: str | None = Field(default=None, max_length=200)
    value: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None


class MemoryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    type: MemoryType
    status: MemoryStatus
    provenance: Provenance
    title: str
    content: str
    key: str | None
    value: str | None
    confidence: float
    tags: list[str] = Field(default_factory=list)
    source_capture_id: uuid.UUID | None
    source_message_id: uuid.UUID | None
    supersedes_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ConflictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    message: str
    existing_memory_id: uuid.UUID | None = None
    proposed_memory_id: uuid.UUID | None = None
    key: str | None = None
    existing_value: str | None = None
    proposed_value: str | None = None


class MemoryCreateResult(BaseModel):
    memory: MemoryItemRead
    conflicts: list[ConflictRead] = Field(default_factory=list)
