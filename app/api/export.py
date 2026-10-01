"""Export API route."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_device, get_project
from app.db.session import get_db
from app.models.device import Device
from app.models.project import Project
from app.services import export as export_service

router = APIRouter(prefix="/projects/{project_id}", tags=["export"])


@router.get("/export")
def export_project(
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    device: Device = Depends(get_current_device),
) -> JSONResponse:
    payload = export_service.export_project(db, project)
    safe_name = project.name.replace(" ", "_").replace("/", "_")
    filename = f"context-core-{safe_name}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
