"""Shared FastAPI dependencies: auth + project scoping."""

import uuid
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_device_token
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project

# Update last_seen_at at most once every 5 minutes to avoid excessive DB writes.
LAST_SEEN_UPDATE_INTERVAL_SECONDS = 300


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )
    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
        )
    return token


def get_current_device(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Device:
    raw_token = _extract_bearer(authorization)
    token_hash = hash_device_token(raw_token)

    device = db.execute(
        select(Device).where(
            Device.token_hash == token_hash,
            Device.revoked.is_(False),
        )
    ).scalar_one_or_none()

    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked device token",
        )

    # Throttled update of last_seen_at
    now = datetime.now(timezone.utc)
    last_seen = device.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    if (
        last_seen is None
        or (now - last_seen).total_seconds() > LAST_SEEN_UPDATE_INTERVAL_SECONDS
    ):
        device.last_seen_at = now
        db.add(device)
        db.commit()

    return device


def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    # Single-user system: every enrolled device may access every project.
    # Project isolation is enforced by scoping every query by project_id.
    return project
