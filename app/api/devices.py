"""Device management routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_device
from app.db.session import get_db
from app.models.device import Device
from app.schemas.auth import DeviceRead, DeviceRevokeResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceRead])
def list_devices(
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> list[DeviceRead]:
    rows = list(
        db.execute(select(Device).order_by(Device.created_at.asc())).scalars()
    )
    return [DeviceRead.model_validate(d) for d in rows]


@router.post(
    "/{device_id}/revoke",
    response_model=DeviceRevokeResponse,
)
def revoke_device(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> DeviceRevokeResponse:
    target = db.get(Device, device_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
        )
    target.revoked = True
    db.commit()
    return DeviceRevokeResponse(device_id=target.id, revoked=True)
