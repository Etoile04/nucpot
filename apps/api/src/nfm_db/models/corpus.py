"""Corpus registry ORM model (NFM-1972 / NFM-1980 AC-5).

A *corpus* identifies the upstream knowledge source that an ingest batch
came from (e.g. ``"ontofuel"`` for OntoFuel-derived batches).  The
``corpus_id`` column is the externally-meaningful slug the API exposes;
the synthetic ``id`` is the database primary key.

CPO contract (AC-5 Decision 3 + Decision 4):
- ``corpus_id`` is UNIQUE — repeated ingests under the same slug must be
  idempotent.  Auto-creation only fires when no row exists.
- ``is_auto_created=True`` flags rows that an integration created on
  first contact (OntoFuel bootstrap); an admin may later back-fill
  ``owner_id`` and ``description``.
- ``owner_id`` is the human user (admin) who registered the corpus, or
  ``NULL`` when auto-created.
- Regular (human) ingest callers that reference an unregistered
  ``corpus_id`` are rejected with 400; only service accounts may
  auto-create.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class Corpus(TimestampMixin, Base):
    """A registered upstream corpus that ingest batches can be tagged with."""

    __tablename__ = "corpus"
    __table_args__ = (
        UniqueConstraint("corpus_id", name="uq_corpus_corpus_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    corpus_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=(
            "External corpus slug used by ingest payloads. UNIQUE; "
            "the API exposes this value, not the synthetic id."
        ),
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Human-readable display name for the corpus.",
    )
    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment="Optional free-form description of the corpus.",
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "User who registered this corpus. NULL when auto-created "
            "by a service account on first ingest."
        ),
    )
    is_auto_created: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment=(
            "True when the row was auto-created by a service-account ingest "
            "on first contact with a fresh corpus_id."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Corpus id={self.id!s} corpus_id={self.corpus_id!r} "
            f"name={self.name!r} is_auto_created={self.is_auto_created}>"
        )


__all__ = ["Corpus"]