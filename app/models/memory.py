import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import MemoryStatus, MemoryType, Provenance, sa_enum


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_project_type_status", "project_id", "type", "status"),
        Index("ix_memory_project_key", "project_id", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[MemoryType] = mapped_column(
        sa_enum(MemoryType, "memory_type", length=32), nullable=False
    )
    status: Mapped[MemoryStatus] = mapped_column(
        sa_enum(MemoryStatus, "memory_status", length=32),
        nullable=False,
        default=MemoryStatus.PROPOSED,
    )
    provenance: Mapped[Provenance] = mapped_column(
        sa_enum(Provenance, "memory_provenance", length=32),
        nullable=False,
        default=Provenance.UNKNOWN,
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured key/value used for deterministic conflict detection, e.g.
    # key="engine", value="Unity" for a DECISION / FACT / REQUIREMENT.
    key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    source_capture_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("captures.id", ondelete="SET NULL"), nullable=True
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
