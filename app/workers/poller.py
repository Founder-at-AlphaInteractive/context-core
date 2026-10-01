"""In-process background poller.

Runs one tick every WORKER_POLL_INTERVAL_SECONDS. Each tick runs in a
worker thread so the sync SQLAlchemy engine is used safely. Errors are
logged; the poller never crashes the API process.

Claim, handler execution, and status transitions operate across separate
transaction boundaries to prevent failed handlers from corrupting job status updates.
"""

from __future__ import annotations

import asyncio

import structlog

from app.config import settings
from app.db.session import SessionLocal
from app.models.job import Job
from app.services.jobs import HANDLERS, claim_next_job, mark_failed, mark_succeeded

# Ensure handlers are imported and registered.
import app.services.job_handlers  # noqa: F401

log = structlog.get_logger(__name__)


def _tick_sync(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    # 1. Claim Phase: dedicated transaction
    try:
        with factory() as claim_db:
            job = claim_next_job(claim_db)
    except Exception:  # noqa: BLE001
        log.exception("poller_claim_failed")
        return

    if job is None:
        return

    # 2. Execution Phase: dedicated session for handler
    handler = HANDLERS.get(job.type)
    if handler is None:
        with factory() as status_db:
            db_job = status_db.get(Job, job.id)
            if db_job is not None:
                mark_failed(
                    status_db,
                    db_job,
                    f"No handler registered for job type '{job.type}'",
                )
        log.warning("unknown_job_type", job_type=job.type, job_id=str(job.id))
        return

    handler_error: str | None = None
    try:
        with factory() as handler_db:
            handler_job = handler_db.get(Job, job.id)
            if handler_job is not None:
                handler(handler_db, handler_job)
                handler_db.commit()
    except Exception as exc:  # noqa: BLE001
        handler_error = f"{type(exc).__name__}: {str(exc)[:500]}"
        log.exception(
            "job_handler_failed",
            job_type=job.type,
            job_id=str(job.id),
            project_id=str(job.project_id) if job.project_id else None,
        )

    # 3. Status Phase: pristine session for recording outcome
    try:
        with factory() as status_db:
            db_job = status_db.get(Job, job.id)
            if db_job is not None:
                if handler_error is not None:
                    mark_failed(status_db, db_job, handler_error)
                else:
                    mark_succeeded(status_db, db_job)
    except Exception:  # noqa: BLE001
        log.exception("failed_to_record_job_status", job_id=str(job.id))


async def run_poller(
    stop_event: asyncio.Event, session_factory=None
) -> None:
    log.info(
        "poller_started",
        interval_seconds=settings.worker_poll_interval_seconds,
    )
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(_tick_sync, session_factory)
            except Exception:  # noqa: BLE001
                log.exception("poller_tick_failed")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.worker_poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
    finally:
        log.info("poller_stopped")
