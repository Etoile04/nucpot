"""RAG index-coverage audit log (NFM-4539 RAG-D + NFM-4742-B / NFM-4744).

One row per drift finding from the ``rag_audit_index_coverage`` Celery
task.  Captures the four shapes the §8.2 contract enumerates:

  - ``reingest`` — completed literature not yet in the LightRAG index.
  - ``noop``     — completed literature already indexed (control row).
  - ``stale``    — orphan entry in the index without a backing
                   completed literature (e.g. a rolled-back dataset).
  - ``error``    — reingest attempt threw; ``error_message`` carries
                   the underlying failure.
  - ``failed``   — processing-timeout reaper (NFM-4742-B) transitioned
                   a row out of LightRAG's ``processing`` bucket;
                   ``failure_kind`` carries the categorical reason
                   (``processing_timeout``) and ``error_message`` the
                   human-readable explanation.

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
        Index("ix_rag_index_audit_failure_kind", "failure_kind"),
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
        comment="reingest | noop | stale | error | failed",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    failure_kind: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment=(
            "NFM-4742-B classification of the ``failed`` action.  One of "
            "``processing_timeout`` (row stranded in the LightRAG "
            "``processing`` bucket past the reaper threshold) or NULL for "
            "pre-NFM-4742 rows.  The 03:30Z beat (NFM-4742-D) filters on "
            "this column to compute the reaper-saved health metric."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RagIndexAuditLog id={self.id!s} routine={self.routine!r} "
            f"action={self.action!r} literature={self.literature_id!s} "
            f"run_date={self.run_date.isoformat()}>"
        )


__all__ = ["RagIndexAuditLog"]
