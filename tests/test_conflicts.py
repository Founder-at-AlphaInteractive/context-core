import uuid
import pytest

from app.models.enums import MemoryStatus, MemoryType, Provenance
from app.models.memory import MemoryItem
from app.services.conflicts import detect_conflicts
from app.services import projects as projects_service


def test_rule1_contradiction_detection(db_session):
    p = projects_service.create_project(db_session, name="Conflict Proj")

    existing = MemoryItem(
        project_id=p.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Engine Decision",
        content="We chose Unity",
        key="engine",
        value="Unity",
    )
    db_session.add(existing)
    db_session.commit()

    # 1. Contradictory candidate (Unreal != Unity)
    candidate_bad = MemoryItem(
        project_id=p.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,
        title="Engine Switch",
        content="Use Unreal",
        key="engine",
        value="Unreal",
    )
    conflicts = detect_conflicts(db_session, project_id=p.id, candidate=candidate_bad)
    assert len(conflicts) >= 1
    kinds = [c.kind for c in conflicts]
    assert "decision_contradiction" in kinds

    # 2. Consistent candidate (same value with whitespace differences)
    candidate_ok = MemoryItem(
        project_id=p.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Engine Confirmation",
        content="Unity confirmed",
        key="engine",
        value=" Unity  ",
    )
    conflicts_ok = detect_conflicts(db_session, project_id=p.id, candidate=candidate_ok)
    contradictions = [c for c in conflicts_ok if c.kind == "decision_contradiction"]
    assert len(contradictions) == 0


def test_rule2_duplicate_completed_task(db_session):
    p = projects_service.create_project(db_session, name="Task Proj")

    completed_task = MemoryItem(
        project_id=p.id,
        type=MemoryType.TASK,
        status=MemoryStatus.COMPLETED,
        provenance=Provenance.USER_CONFIRMED,
        title="Init DB",
        content="Set up database tables",
        key="setup_db",
    )
    db_session.add(completed_task)
    db_session.commit()

    # Proposed new task with same key
    new_task = MemoryItem(
        project_id=p.id,
        type=MemoryType.TASK,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,
        title="Setup DB again",
        content="Create db tables",
        key="setup_db",
    )
    conflicts = detect_conflicts(db_session, project_id=p.id, candidate=new_task)
    kinds = [c.kind for c in conflicts]
    assert "duplicate_completed_task" in kinds


def test_rule3_provenance_override_warning(db_session):
    p = projects_service.create_project(db_session, name="Provenance Proj")

    strong_item = MemoryItem(
        project_id=p.id,
        type=MemoryType.REQUIREMENT,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,  # rank 100
        title="Auth requirement",
        content="Must use opaque tokens",
        key="auth_type",
        value="opaque_token",
    )
    db_session.add(strong_item)
    db_session.commit()

    # Weaker item attempting to propose alternative
    weaker_item = MemoryItem(
        project_id=p.id,
        type=MemoryType.REQUIREMENT,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,  # rank 40
        title="Auth alternative",
        content="Let's use JWT instead",
        key="auth_type",
        value="jwt",
    )
    conflicts = detect_conflicts(db_session, project_id=p.id, candidate=weaker_item)
    kinds = [c.kind for c in conflicts]
    assert "provenance_override" in kinds
    assert "requirement_contradiction" in kinds


def test_conflict_detection_cross_project_isolation(db_session):
    p1 = projects_service.create_project(db_session, name="P1")
    p2 = projects_service.create_project(db_session, name="P2")

    mem_p1 = MemoryItem(
        project_id=p1.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="P1 Engine",
        content="Unity",
        key="engine",
        value="Unity",
    )
    db_session.add(mem_p1)
    db_session.commit()

    # Candidate in P2 with same key and different value should NOT conflict with P1
    candidate_p2 = MemoryItem(
        project_id=p2.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="P2 Engine",
        content="Unreal",
        key="engine",
        value="Unreal",
    )
    conflicts_p2 = detect_conflicts(db_session, project_id=p2.id, candidate=candidate_p2)
    assert len(conflicts_p2) == 0


def test_conflict_detection_is_side_effect_free(db_session):
    p = projects_service.create_project(db_session, name="P Side Effect")
    existing = MemoryItem(
        project_id=p.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.ACTIVE,
        provenance=Provenance.USER_CONFIRMED,
        title="Existing",
        content="Val1",
        key="k1",
        value="Val1",
    )
    db_session.add(existing)
    db_session.commit()

    cand = MemoryItem(
        project_id=p.id,
        type=MemoryType.DECISION,
        status=MemoryStatus.PROPOSED,
        provenance=Provenance.AI_PROPOSAL,
        title="Candidate",
        content="Val2",
        key="k1",
        value="Val2",
    )
    detect_conflicts(db_session, project_id=p.id, candidate=cand)

    # Check database state: exactly 1 item exists, unmodified
    all_items = db_session.query(MemoryItem).all()
    assert len(all_items) == 1
    assert all_items[0].value == "Val1"
    assert all_items[0].status == MemoryStatus.ACTIVE
