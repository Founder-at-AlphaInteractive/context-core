import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    state_json: dict
    created_by_device_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class ProjectStateUpdate(BaseModel):
    """Patch-style update. Merged server-side into the current state_json
    via deep merge, then stored as a new version.

    Set expected_version to require optimistic concurrency control.
    """

    patch: dict = Field(default_factory=dict)
    expected_version: int | None = None
    note: str | None = None
    # If true, replace state_json entirely rather than deep-merging.
    replace: bool = False
