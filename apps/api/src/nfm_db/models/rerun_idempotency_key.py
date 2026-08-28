"""RerunIdempotencyKey ORM model.

NFM-3543-D (NFM-3598): persisted idempotency store for the
``POST /jobs/{id}/steps/{name}/rerun`` endpoint. A duplicate request
with the same ``Idempotency-Key`` header (or ``client_request_id``
body field) within a 24h window replays the original 202 response
with ``Idempotent-Replayed: true`` instead of kicking off a new
execution.

Periodic cleanup of rows older than 24h is out of scope for this
issue — see ``docs/api/jobs.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class RerunIdempotencyKey(Base):
    """One row per accepted rerun request.

    The (idempotency_key) primary key enforces the at-most-once
    execution contract; the (job_id, step_name) pair is used to
    detect concurrent in-flight reruns (409 ``step_in_flight``).
    """

    __tablename__ = "rerun_idempotency_keys"

    idempotency_key: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        nullable=False,
        comment=(
            "Client-supplied token: ``Idempotency-Key`` header or "
            "``client_request_id`` body field."
        ),
    )
    track_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        comment=(
            "track_id returned in the original 202 response. Replays "
            "echo this same id."
        ),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        comment="extraction_jobs.id the rerun was bound to.",
    )
    step_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Pipeline step name the rerun was bound to.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=None,
        comment=(
            "Insertion time. Periodic cleanup job removes rows older "
            "than 24h; see docs/api/jobs.md."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RerunIdempotencyKey key={self.idempotency_key!r} "
            f"job={self.job_id!s} step={self.step_name!r} "
            f"track_id={self.track_id!s}>"
        )
