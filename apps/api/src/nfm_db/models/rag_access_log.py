"""RAG access log ORM model (NFM-4539 RAG-B).

A single row per ``POST /api/v1/lightrag/query`` call.  Surfaces the
operational telemetry required by AC-7 and the §3.3 / §5 contract:

  - ``mode``         — query mode requested (local/global/hybrid/mix/naive)
  - ``was_fallback`` — whether the response was rescued via the ILIKE path
  - ``was_cached``   — whether the sidecar reported a cache hit
  - ``query_kind``   — semantic | text | ilike; tracks the resolved path
  - ``result_count`` — number of references in the response
  - ``time_total``   — end-to-end wall-clock seconds for the call

Stored with a SQL ``TIMESTAMPTZ`` index for the 7-day P95 SLA roll-ups
that drive the AC-5 / AC-8 dashboards.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class RagAccessLog(Base):
    """One row per ``/api/v1/lightrag/query`` invocation."""

    __tablename__ = "rag_access_log"
    __table_args__ = (
        Index("ix_rag_access_log_ts", "ts"),
        Index("ix_rag_access_log_mode_ts", "mode", "ts"),
        Index("ix_rag_access_log_was_fallback", "was_fallback"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ts: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Wall-clock time the request was logged (server-side).",
    )
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Query mode (local/global/hybrid/mix/naive).",
    )
    was_fallback: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True iff the response was rescued via ILIKE.",
    )
    was_cached: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True iff the sidecar reported a cache hit.",
    )
    query_kind: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="Resolved path: semantic | text | ilike.",
    )
    result_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of references in the response payload.",
    )
    time_total: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="End-to-end wall-clock seconds for the query call.",
    )
    error_message: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="First-class error capture when fallback fires.",
    )

    def __repr__(self) -> str:
        return (
            f"<RagAccessLog id={self.id!s} mode={self.mode!r} "
            f"was_fallback={self.was_fallback} time_total={self.time_total:.3f}>"
        )


__all__ = ["RagAccessLog"]
