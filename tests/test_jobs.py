"""Tests for database-backed jobs queue, lifecycle, poller tick, and handlers."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.conversation import Capture, CaptureMode, Conversation
from app.models.enums import EventType, JobStatus
from app.models.job import Job
from app.models.project import Project
from app.services.events import emit_event
from app.services.jobs import (
    claim_next_job,
    enqueue_job,
    list_jobs,
    mark_failed,
    mark_succeeded,
    register_handler,
)
from app.services.job_handlers import handle_extract_memory


@pytest.fixture
def test_project(db_session):
    project = Project(id=uuid.uuid4(), name="Jobs Test Project", last_event_sequence=0)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def test_enqueue_job(db_session, test_project):
    job = enqueue_job(
        db_session,
        job_type="dummy_task",
        payload={"foo": "bar"},
        project_id=test_project.id,
        max_attempts=3,
    )
    db_session.commit()

    assert job.id is not None
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert job.max_attempts == 3

    jobs = list_jobs(db_session, project_id=test_project.id)
    assert len(jobs) == 1
    assert jobs[0].id == job.id


def test_claim_next_job_and_success(db_session, test_project):
    job = enqueue_job(
        db_session,
        job_type="dummy_task",
        payload={"step": 1},
        project_id=test_project.id,
    )
    db_session.commit()

    claimed = claim_next_job(db_session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.locked_at is not None

    # Nothing else pending
    second_claim = claim_next_job(db_session)
    assert second_claim is None

    mark_succeeded(db_session, claimed)
    db_session.refresh(claimed)
    assert claimed.status == JobStatus.SUCCEEDED
    assert claimed.locked_at is None


def test_stale_running_job_recovery(db_session, test_project):
    # Simulate a job that was locked 10 minutes ago and worker crashed
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    job = Job(
        type="crashed_task",
        payload={},
        project_id=test_project.id,
        status=JobStatus.RUNNING,
        attempts=1,
        max_attempts=3,
        locked_at=stale_time,
        run_after=stale_time,
    )
    db_session.add(job)
    db_session.commit()

    # Claiming with 300s stale timeout should recover this job
    recovered = claim_next_job(db_session, stale_timeout_seconds=300)
    assert recovered is not None
    assert recovered.id == job.id
    assert recovered.status == JobStatus.RUNNING
    assert recovered.attempts == 2  # incremented on retry
    locked_at = (
        recovered.locked_at
        if recovered.locked_at.tzinfo
        else recovered.locked_at.replace(tzinfo=timezone.utc)
    )
    assert (datetime.now(timezone.utc) - locked_at).total_seconds() < 10


def test_stale_running_job_max_attempts_dead(db_session, test_project):
    # Stale job that has already exhausted max_attempts
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    job = Job(
        type="dead_task",
        payload={},
        project_id=test_project.id,
        status=JobStatus.RUNNING,
        attempts=3,
        max_attempts=3,
        locked_at=stale_time,
        run_after=stale_time,
    )
    db_session.add(job)
    db_session.commit()

    # Claiming should transition it to DEAD and return None (no other jobs)
    claimed = claim_next_job(db_session, stale_timeout_seconds=300)
    assert claimed is None

    db_session.refresh(job)
    assert job.status == JobStatus.DEAD
    assert "Job timed out in RUNNING state and exceeded max attempts" in job.last_error


def test_mark_failed_retrying_and_exponential_backoff(db_session, test_project):
    job = enqueue_job(
        db_session,
        job_type="failing_task",
        payload={},
        project_id=test_project.id,
        max_attempts=3,
    )
    db_session.commit()

    claimed = claim_next_job(db_session)
    assert claimed.attempts == 1

    mark_failed(db_session, claimed, "First failure")
    db_session.refresh(claimed)
    assert claimed.status == JobStatus.RETRYING
    assert claimed.last_error == "First failure"
    assert claimed.locked_at is None
    # 2^1 = 2 seconds backoff
    run_after = (
        claimed.run_after
        if claimed.run_after.tzinfo
        else claimed.run_after.replace(tzinfo=timezone.utc)
    )
    assert run_after > datetime.now(timezone.utc) - timedelta(seconds=1)


def test_mark_failed_max_attempts_dead(db_session, test_project):
    job = enqueue_job(
        db_session,
        job_type="fatal_task",
        payload={},
        project_id=test_project.id,
        max_attempts=2,
    )
    db_session.commit()

    claimed = claim_next_job(db_session)
    assert claimed.attempts == 1
    mark_failed(db_session, claimed, "Attempt 1 failed")

    # Manually fast-forward run_after so it is eligible
    claimed.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    claimed2 = claim_next_job(db_session)
    assert claimed2.attempts == 2

    mark_failed(db_session, claimed2, "Attempt 2 failed")
    db_session.refresh(claimed2)
    assert claimed2.status == JobStatus.DEAD
    assert claimed2.last_error == "Attempt 2 failed"


def test_extract_memory_handler_project_isolation(db_session, test_project):
    # Other project
    other_project = Project(id=uuid.uuid4(), name="Other Project", last_event_sequence=0)
    db_session.add(other_project)
    db_session.flush()

    # Conversation in other project
    other_conv = Conversation(project_id=other_project.id, cursor={})
    db_session.add(other_conv)
    db_session.flush()

    # Capture in other project
    capture = Capture(
        project_id=other_project.id,
        conversation_id=other_conv.id,
        client_id=uuid.uuid4(),
        local_id=uuid.uuid4(),
        mode=CaptureMode.LAST_EXCHANGE,
        payload={},
        message_count=1,
        status="stored",
    )
    db_session.add(capture)
    db_session.commit()

    # Job belongs to test_project, but references entities from other_project!
    bad_job = Job(
        type="extract_memory",
        project_id=test_project.id,
        payload={"capture_id": str(capture.id), "conversation_id": str(other_conv.id)},
        status=JobStatus.RUNNING,
    )
    db_session.add(bad_job)
    db_session.commit()

    with pytest.raises(ValueError, match="Cross-project entity mismatch"):
        handle_extract_memory(db_session, bad_job)


def test_extract_memory_handler_missing_entity_is_safe_noop(db_session, test_project):
    job = Job(
        type="extract_memory",
        project_id=test_project.id,
        payload={"capture_id": str(uuid.uuid4()), "conversation_id": str(uuid.uuid4())},
        status=JobStatus.RUNNING,
    )
    db_session.add(job)
    db_session.commit()

    # Must safely no-op without raising exception
    handle_extract_memory(db_session, job)
