import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=50)
    registration_secret: str = Field(min_length=1)


class DeviceRegisterResponse(BaseModel):
    device_id: uuid.UUID
    name: str
    platform: str
    token: str  # raw token, shown exactly once
    created_at: datetime


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    platform: str
    revoked: bool
    last_seen_at: datetime | None
    created_at: datetime


class DeviceRevokeResponse(BaseModel):
    device_id: uuid.UUID
    revoked: bool
