import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import EventType


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    sequence: int
    type: EventType
    entity_id: uuid.UUID | None
    payload: dict
    client_id: uuid.UUID | None
    created_at: datetime


class EventPage(BaseModel):
    events: list[EventRead]
    latest_sequence: int
