"""Context Core backend entrypoint."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import (
    ai_team,
    auth,
    captures,
    combine,
    context,
    devices,
    events,
    export,
    handoffs,
    memory,
    projects,
    search,
    state,
)
from app.config import settings
from app.logging_config import configure_logging
from app.websocket import bridge, router as ws_router
from app.websocket.manager import get_manager
from app.workers.poller import run_poller

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    loop = asyncio.get_running_loop()
    manager = get_manager()
    bridge.install(loop, manager)

    stop_event = asyncio.Event()
    poller_task = None
    if settings.worker_enabled:
        poller_task = asyncio.create_task(run_poller(stop_event))

    log.info(
        "context_core_started",
        environment=settings.environment,
        groq_configured=bool(settings.groq_api_key),
    )

    try:
        yield
    finally:
        stop_event.set()
        if poller_task is not None:
            try:
                await asyncio.wait_for(poller_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                poller_task.cancel()
        bridge.uninstall()
        log.info("context_core_stopped")


app = FastAPI(
    title="Context Core",
    version="0.1.0",
    description=(
        "Persistent project memory for multi-AI, multi-device workflows. "
        "Single-user private system."
    ),
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response
    except Exception:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        error_response = JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )
        error_response.headers["X-Request-ID"] = request_id
        return error_response
    finally:
        structlog.contextvars.unbind_contextvars("request_id")


@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "groq_configured": bool(settings.groq_api_key),
    }


# ---- Routers --------------------------------------------------------------
app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(ai_team.router)
app.include_router(projects.router)
app.include_router(captures.router)
app.include_router(memory.router)
app.include_router(state.router)
app.include_router(events.router)
app.include_router(context.router)
app.include_router(combine.router)
app.include_router(handoffs.router)
app.include_router(search.router)
app.include_router(export.router)
app.include_router(ws_router)
