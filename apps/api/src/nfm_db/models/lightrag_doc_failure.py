"""Per-document failure tracking (NFM-4743).

NFM-4738 F-3 audit (rag-comprehensive-test-report-2026-09-11.md) showed
the LightRAG ``failed`` bucket is 30 rows split as 20 dedupe-rejected
("Identical content already exists" / "File name already exists") and
10 real chunking/extraction failures.  Both flavours were conflated in
the ``failed`` count, masking the real errors and polluting the health
metric that drives operator alerting.

This model mirrors the failed rows from LightRAG's ``/documents`` API
into a queryable PG table with an explicit ``failure_kind`` column:

  * ``duplicate``         — dedupe rejection ("already exists" / "Identical
                            content" / "File name already exists" / dedupe
                            key match).
  * ``error``             — genuine chunking/extraction failure.
  * ``processing_timeout``— pipeline timed out before completion.

Back-compat (per NFM-4743 AC): any row whose error_message cannot be
classified defaults to ``"error"`` so legacy rows already present in
the bucket land in the historical "real failure" count rather than
disappearing.

``doc_id`` carries the LightRAG row id (a stable per-document token
that survives replay); ``file_source`` carries the original
``data_source:<uuid>`` / ``nfm-4505-fresh-<uuid>`` marker so the
``rag_audit_index_coverage`` reconciliation can join failures back
to the completed literature set.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class LightragDocFailure(Base):
    """One row per LightRAG document that landed in the ``failed`` bucket.

    Populated by ``nfm_db.services.lightrag_failure.record_doc_failures``
    from the LightRAG ``/documents`` API envelope (the ``failed`` bucket).
    """

    __tablename__ = "lightrag_doc_failure"
    __table_args__ = (
        Index("ix_lightrag_doc_failure_kind", "failure_kind"),
        Index("ix_lightrag_doc_failure_last_seen_at", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    doc_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        unique=True,
        comment="LightRAG row id; stable across replay.",
    )
    file_source: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Original file_source / data_source:<uuid> marker, when present.",
    )
    failure_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="duplicate | error | processing_timeout",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Raw error_msg from LightRAG; preserved for forensic queries.",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="First time we observed the doc in the failed bucket.",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Most recent observation; refreshed on every replay.",
    )

    def __repr__(self) -> str:
        return (
            f"<LightragDocFailure id={self.id!s} doc_id={self.doc_id!r} "
            f"failure_kind={self.failure_kind!r}>"
        )


__all__ = ["LightragDocFailure"]
