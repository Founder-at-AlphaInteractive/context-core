"""Device registration and self-management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_device
from app.auth.security import (
    generate_device_token,
    hash_device_token,
    verify_device_registration_secret,
)
from app.db.session import get_db
from app.models.device import Device
from app.schemas.auth import (
    DeviceRead,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/device/register",
    response_model=DeviceRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_device(
    payload: DeviceRegisterRequest,
    db: Session = Depends(get_db),
) -> DeviceRegisterResponse:
    if not verify_device_registration_secret(payload.registration_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device registration secret",
        )

    raw_token = generate_device_token()
    device = Device(
        name=payload.name,
        platform=payload.platform,
        token_hash=hash_device_token(raw_token),
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    return DeviceRegisterResponse(
        device_id=device.id,
        name=device.name,
        platform=device.platform,
        token=raw_token,
        created_at=device.created_at,
    )


@router.get("/device/me", response_model=DeviceRead)
def read_current_device(
    device: Device = Depends(get_current_device),
) -> DeviceRead:
    return DeviceRead.model_validate(device)
