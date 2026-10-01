"""Database session and commit-time hook (updated in Part 6).

After a successful root commit, queued events are published to the WebSocket
bridge. Nested transactions / savepoints (e.g. begin_nested()) maintain their
own event frames so that soft rollbacks only discard events from the failed
savepoint without destroying outer transaction events.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_PENDING_STACK_KEY = "_pending_events_stack"


def _get_stack(session: Session) -> list[list[dict]]:
    return session.info.setdefault(_PENDING_STACK_KEY, [[]])


@sa_event.listens_for(Session, "after_transaction_create")
def _on_transaction_create(session: Session, transaction) -> None:
    if transaction.nested:
        _get_stack(session).append([])


@sa_event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    stack = _get_stack(session)
    if session.in_nested_transaction():
        # Savepoint committed: merge frame into parent frame
        if len(stack) > 1:
            frame = stack.pop()
            stack[-1].extend(frame)
    else:
        # Root commit: publish all surviving events across all frames
        session.info.pop(_PENDING_STACK_KEY, None)
        all_events = [ev for frame in stack for ev in frame]
        if not all_events:
            return
        try:
            from app.websocket.bridge import publish_event_sync
        except Exception:
            return
        for payload in all_events:
            publish_event_sync(payload)


@sa_event.listens_for(Session, "after_soft_rollback")
def _after_soft_rollback(session: Session, previous_transaction) -> None:
    # Savepoint rolled back: discard only the top frame
    stack = _get_stack(session)
    if len(stack) > 1:
        stack.pop()


@sa_event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    # Root rollback: discard everything
    if not session.in_nested_transaction():
        session.info.pop(_PENDING_STACK_KEY, None)
