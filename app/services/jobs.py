"""Database-backed job queue.

A persistent job table + a single in-process poller. No Redis, no
Celery. Jobs survive process restarts. Failed jobs are retried with
exponential backoff up to max_attempts; then they are marked DEAD.
Stale RUNNING jobs (e.g. from process crashes) are recovered safely.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import EventType, JobStatus
from app.models.job import Job
from app.services.events import emit_event

DEFAULT_STALE_TIMEOUT_SECONDS = 300  # 5 minutes

JobHandler = Callable[[Session, Job], None]
HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        HANDLERS[job_type] = fn
        return fn

    return decorator


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict,
    project_id: uuid.UUID | None = None,
    max_attempts: int | None = None,
    run_after: datetime | None = None,
) -> Job:
    job = Job(
        type=job_type,
        payload=payload or {},
        project_id=project_id,
        status=JobStatus.PENDING,
        attempts=0,
        max_attempts=max_attempts or settings.worker_max_attempts,
        run_after=run_after or datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    if project_id is not None:
        emit_event(
            db,
            project_id=project_id,
            event_type=EventType.JOB_STATE_CHANGED,
            entity_id=job.id,
            payload={"job_type": job_type, "status": JobStatus.PENDING.value},
        )

    return job


def claim_next_job(
    db: Session, *, stale_timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS
) -> Job | None:
    """Atomically claim one ready job for processing.
    
    Includes recovery for stale RUNNING jobs that were interrupted by a process crash.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=stale_timeout_seconds)

    while True:
        stmt = (
            select(Job)
            .where(
                or_(
                    and_(
                        Job.status.in_([JobStatus.PENDING, JobStatus.RETRYING]),
                        Job.run_after <= now,
                    ),
                    and_(
                        Job.status == JobStatus.RUNNING,
                        Job.locked_at <= stale_cutoff,
                    ),
                )
            )
            .order_by(Job.run_after.asc(), Job.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = db.execute(stmt).scalar_one_or_none()
        if job is None:
            return None

        # Check if this was a stale RUNNING job
        if job.status == JobStatus.RUNNING:
            if job.attempts >= job.max_attempts:
                # Exceeded max attempts: transition to DEAD
                job.status = JobStatus.DEAD
                job.last_error = "Job timed out in RUNNING state and exceeded max attempts"
                job.locked_at = None
                if job.project_id is not None:
                    emit_event(
                        db,
                        project_id=job.project_id,
                        event_type=EventType.JOB_STATE_CHANGED,
                        entity_id=job.id,
                        payload={
                            "job_type": job.type,
                            "status": JobStatus.DEAD.value,
                            "error": job.last_error,
                        },
                    )
                db.commit()
                # Try claiming the next job
                continue

        # Normal claim or recovery of retryable stale job
        job.status = JobStatus.RUNNING
        job.locked_at = now
        job.attempts = job.attempts + 1
        db.commit()
        db.refresh(job)
        return job


def mark_succeeded(db: Session, job: Job) -> None:
    job.status = JobStatus.SUCCEEDED
    job.last_error = None
    job.locked_at = None
    if job.project_id is not None:
        emit_event(
            db,
            project_id=job.project_id,
            event_type=EventType.JOB_STATE_CHANGED,
            entity_id=job.id,
            payload={"job_type": job.type, "status": JobStatus.SUCCEEDED.value},
        )
    db.commit()


def mark_failed(db: Session, job: Job, error_message: str) -> None:
    # Truncate and sanitize error message to prevent leaking sensitive context
    job.last_error = error_message[:2000]
    job.locked_at = None

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
    else:
        job.status = JobStatus.RETRYING
        # Exponential backoff: 2^attempts seconds, capped at 10 minutes (600 seconds)
        backoff_seconds = min(600, 2 ** max(1, job.attempts))
        job.run_after = datetime.now(timezone.utc) + timedelta(
            seconds=backoff_seconds
        )

    if job.project_id is not None:
        emit_event(
            db,
            project_id=job.project_id,
            event_type=EventType.JOB_STATE_CHANGED,
            entity_id=job.id,
            payload={
                "job_type": job.type,
                "status": job.status.value,
                "error": job.last_error,
            },
        )
    db.commit()


def list_jobs(
    db: Session,
    *,
    project_id: uuid.UUID | None = None,
    status_filter: JobStatus | None = None,
    limit: int = 100,
) -> list[Job]:
    stmt = select(Job)
    if project_id is not None:
        stmt = stmt.where(Job.project_id == project_id)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    stmt = stmt.order_by(Job.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars())
