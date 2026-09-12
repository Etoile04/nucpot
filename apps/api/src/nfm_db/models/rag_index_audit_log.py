"""RAG index-coverage audit log (NFM-4539 RAG-D).

One row per drift finding from the ``rag_audit_index_coverage`` Celery
task.  Captures the four shapes the §8.2 contract enumerates:

  - ``reingest`` — completed literature not yet in the LightRAG index.
  - ``noop``     — completed literature already indexed (control row).
  - ``stale``    — orphan entry in the index without a backing
                   completed literature (e.g. a rolled-back dataset).
  - ``error``    — reingest attempt threw; ``error_message`` carries
                   the underlying failure.

``(routine, literature_id, run_date)`` is a composite unique constraint
so retries are idempotent: a partial-failure replay that revisits the
same literature on the same run date raises IntegrityError, and the
writer converts it to a silent skip.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class RagIndexAuditLog(Base):
    """One row per RAG index-coverage decision (NFM-4539 RAG-D §8.2)."""

    __tablename__ = "rag_index_audit_log"
    __table_args__ = (
        UniqueConstraint(
            "routine",
            "literature_id",
            "run_date",
            name="uq_rag_index_audit_natural_key",
        ),
        Index("ix_rag_index_audit_run_date", "run_date"),
        Index("ix_rag_index_audit_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    routine: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="rag_audit_index_coverage",
    )
    run_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    literature_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        comment="Completed literature UUID; NULL for index-orphan rows.",
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="reingest | noop | stale | error | failed_duplicate "
        "| failed_error | failed_empty | processing_reaped | bucket_counts",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # NFM-4742 F-3 §3.2: segregate the ``failed`` bucket into the
    # categorically distinct outcomes the LightRAG sidecar reports
    # (``duplicate`` = dedupe, ``error`` = real failure, ``empty`` =
    # failed-but-no-message).  Mirrors migration 090; the column is
    # nullable so pre-NFM-4742 audit rows keep their shape and
    # historical backfill stays out of scope.
    failure_reason: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="duplicate | error | empty | timeout | unknown",
    )

    def __repr__(self) -> str:
        return (
            f"<RagIndexAuditLog id={self.id!s} routine={self.routine!r} "
            f"action={self.action!r} failure_reason={self.failure_reason!r} "
            f"literature={self.literature_id!s} "
            f"run_date={self.run_date.isoformat()}>"
        )


__all__ = ["RagIndexAuditLog"]
