"""ExtractionChunk ORM model.

Represents a single text chunk produced by either the V1 chunking step
(``ExtractionChunkData`` from ``nfm_db.services.chunker``) or the V2
strangler-fig pipeline (NFM-2687, parent: NFM-2676). Chunks are the
atomic units fed to downstream extraction and mapping steps.

V2 contract (NFM-2687)
----------------------
Each V2 row carries a ``step_name`` (which pipeline step produced it)
and a ``_source_span`` provenance payload. The V2 ``source_span`` schema
is::

    {
        "start_offset": int,        # non-negative
        "end_offset":   int,        # non-negative, >= start_offset
        "section_id":   str | None,
    }

V1 rows remain readable: the underlying ``source_span`` column is
JSONB and accepts any dict. The ``_source_span`` Python property
provides a validated view against the V2 schema. New V2 writes should
go through ``ExtractionChunk.upsert_by_span_hash`` which sets
``source_span_hash`` automatically for idempotent upserts.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin

__all__ = [
    "ExtractionChunk",
    "SourceSpan",
    "SourceSpanValidationError",
    "compute_source_span_hash",
    "validate_source_span",
]


# ---------------------------------------------------------------------------
# V2 source_span schema validation
# ---------------------------------------------------------------------------


class SourceSpanValidationError(ValueError):
    """Raised when a source_span dict violates the V2 provenance schema."""


def validate_source_span(span: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a V2 ``source_span`` payload in place.

    Schema::

        {"start_offset": int,
         "end_offset":   int,
         "section_id":   str | None}

    Constraints:

    * ``start_offset`` and ``end_offset`` must be ``int`` (not ``bool``).
    * Both offsets must be non-negative.
    * ``end_offset`` must be ``>= start_offset``.
    * ``section_id`` is optional; when present, it must be ``str``.

    ``None`` is accepted and returned unchanged. Returns the input dict on
    success (for chaining). Raises :class:`SourceSpanValidationError`
    on any schema violation.
    """
    if span is None:
        return None
    if not isinstance(span, dict):
        raise SourceSpanValidationError(
            f"source_span must be a dict, got {type(span).__name__}"
        )

    if "start_offset" not in span or "end_offset" not in span:
        raise SourceSpanValidationError(
            "source_span must contain both 'start_offset' and 'end_offset' keys"
        )

    start = span["start_offset"]
    end = span["end_offset"]

    # Reject bool explicitly — bool is a subclass of int in Python.
    if not isinstance(start, int) or isinstance(start, bool):
        raise SourceSpanValidationError(
            f"start_offset must be int, got {type(start).__name__}"
        )
    if not isinstance(end, int) or isinstance(end, bool):
        raise SourceSpanValidationError(
            f"end_offset must be int, got {type(end).__name__}"
        )

    if start < 0:
        raise SourceSpanValidationError(
            f"start_offset must be non-negative, got {start}"
        )
    if end < 0:
        raise SourceSpanValidationError(
            f"end_offset must be non-negative, got {end}"
        )
    if end < start:
        raise SourceSpanValidationError(
            f"end_offset ({end}) must be >= start_offset ({start})"
        )

    section_id = span.get("section_id")
    if section_id is not None and not isinstance(section_id, str):
        raise SourceSpanValidationError(
            f"section_id must be str or None, got {type(section_id).__name__}"
        )

    return span


def compute_source_span_hash(
    job_id: uuid.UUID,
    step_name: str,
    source_span: dict[str, Any] | None,
) -> str:
    """Deterministic SHA-256 of the (job_id, step_name, source_span) triple.

    The same triple always produces the same hash, so it can be used as
    an idempotency key for the
    :meth:`ExtractionChunk.upsert_by_span_hash` upsert method. ``None``
    for ``source_span`` is normalised to a stable JSON ``null``.
    """
    payload = {
        "job_id": str(job_id),
        "step_name": step_name,
        "source_span": source_span,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# V2 dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSpan:
    """V2 source_span provenance payload (NFM-2687).

    Attributes:
        start_offset: Character-level start offset into the source
            document. Must be non-negative.
        end_offset: Character-level end offset (exclusive). Must be
            non-negative and ``>= start_offset``.
        section_id: Optional section identifier (e.g. heading slug).
    """

    start_offset: int
    end_offset: int
    section_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start_offset, int) or isinstance(self.start_offset, bool):
            raise SourceSpanValidationError(
                f"start_offset must be int, got {type(self.start_offset).__name__}"
            )
        if not isinstance(self.end_offset, int) or isinstance(self.end_offset, bool):
            raise SourceSpanValidationError(
                f"end_offset must be int, got {type(self.end_offset).__name__}"
            )
        if self.start_offset < 0:
            raise SourceSpanValidationError(
                f"start_offset must be non-negative, got {self.start_offset}"
            )
        if self.end_offset < 0:
            raise SourceSpanValidationError(
                f"end_offset must be non-negative, got {self.end_offset}"
            )
        if self.end_offset < self.start_offset:
            raise SourceSpanValidationError(
                f"end_offset ({self.end_offset}) must be >= "
                f"start_offset ({self.start_offset})"
            )
        if self.section_id is not None and not isinstance(self.section_id, str):
            raise SourceSpanValidationError(
                f"section_id must be str or None, got "
                f"{type(self.section_id).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical V2 dict for storage / hashing."""
        return {
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "section_id": self.section_id,
        }


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class ExtractionChunk(TimestampMixin, Base):
    """A single text chunk from the chunking step (NFM-2567 / NFM-2687).

    V2 contract (NFM-2687):
      * ``step_name`` identifies which pipeline step produced the chunk.
      * ``source_span`` carries the V2 schema documented at module top.
      * ``source_span_hash`` is a SHA-256 used by
        :meth:`upsert_by_span_hash` for idempotency.
      * ``metadata_`` holds arbitrary JSON metadata (trailing underscore
        avoids the SQLAlchemy ``MetaData`` name collision).
      * ``token_estimate`` is the V2 token count. ``token_count`` is the
        V1 column and is preserved for backward compatibility with the
        existing chunker persistence path.

    The V1 chunker continues to work: every new column is nullable, and
    the partial unique index on (job_id, step_name, source_span_hash) is
    scoped to rows where both ``step_name`` and ``source_span_hash`` are
    non-NULL, so V1 rows are not constrained.
    """

    __tablename__ = "extraction_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Parent job ---
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        comment="Parent extraction job that produced this chunk.",
    )

    # --- V2 step identity (NFM-2687) ---
    step_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Pipeline step that produced this chunk (e.g. 'chunk', 'extract').",
    )

    # --- Source provenance ---
    source_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Source identifier (e.g. page number, section heading).",
    )

    # --- Content ---
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The chunk text produced by the chunker.",
    )

    # --- Source offsets (JSONB) ---
    # V1 stored ``{"start": int, "end": int}``; V2 (NFM-2687) uses
    # ``{"start_offset": int, "end_offset": int, "section_id": str | None}``.
    # The column is schema-agnostic; V2 callers should use the
    # ``_source_span`` property (or :func:`validate_source_span`) to
    # enforce the V2 contract.
    source_span: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment=(
            "Source file offsets. V1: {\"start\": int, \"end\": int}. "
            "V2 (NFM-2687): {\"start_offset\": int, \"end_offset\": int, "
            "\"section_id\": str | None}."
        ),
    )

    # --- V2 idempotency (NFM-2687) ---
    source_span_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment=(
            "SHA-256 of (job_id, step_name, source_span) for upsert "
            "idempotency. Unique with step_name and job_id when set."
        ),
    )

    # --- Ordering ---
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Sequential index of this chunk within the job.",
    )

    # --- V2 token estimation ---
    token_estimate: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Estimated token count for downstream batching (V2).",
    )

    # --- V1 token estimation (preserved for V1 chunker compat) ---
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Estimated token count (V1 legacy). Prefer token_estimate.",
    )

    # --- V2 flexible metadata ---
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment=(
            "Arbitrary chunk metadata. Trailing underscore avoids "
            "SQLAlchemy MetaData name collision."
        ),
    )

    # --- Partial unique index for V2 idempotency (NFM-2687) ---
    # Only enforced when both ``step_name`` and ``source_span_hash`` are
    # non-NULL, so V1 rows (which lack these columns) are not constrained.
    # On PostgreSQL this becomes a partial UNIQUE INDEX with a WHERE
    # clause; on other dialects SQLAlchemy emits a regular unique index,
    # which is acceptable for tests (NULLs treated as distinct).
    __table_args__ = (
        Index(
            "ix_extraction_chunks_v2_idempotency",
            "job_id",
            "step_name",
            "source_span_hash",
            unique=True,
            postgresql_where=(
                "step_name IS NOT NULL AND source_span_hash IS NOT NULL"
            ),
        ),
    )

    @property
    def _source_span(self) -> dict[str, Any] | None:
        """The validated V2 source_span view (read-only).

        Returns the raw ``source_span`` column value. V2 callers should
        pair this with :func:`validate_source_span` to enforce the new
        schema. Use the setter to write a V2-shaped value with
        validation.
        """
        return self.source_span

    @_source_span.setter
    def _source_span(self, value: dict[str, Any] | None) -> None:
        """Set ``source_span`` after V2 validation.

        ``None`` clears the column. Any other value is validated against
        the V2 schema; on failure a :class:`SourceSpanValidationError`
        is raised and the column is left unchanged.
        """
        validate_source_span(value)
        self.source_span = value

    @classmethod
    def upsert_by_span_hash(
        cls,
        session: Any,
        *,
        job_id: uuid.UUID,
        step_name: str,
        content: str,
        source_span: dict[str, Any] | None,
        chunk_index: int,
        token_estimate: int | None = None,
        metadata_: dict[str, Any] | None = None,
        source_reference: str | None = None,
    ) -> ExtractionChunk:
        """Idempotent upsert keyed by ``(job_id, step_name, source_span_hash)``.

        On first call for a given triple, a new row is created (added to
        ``session`` but not flushed). On subsequent calls with the same
        triple, the existing row is returned unchanged — caller-supplied
        ``content`` / ``chunk_index`` / etc. are ignored, matching the
        AC requirement that "same triple returns same row".

        The ``source_span`` payload is validated against the V2 schema
        (see :func:`validate_source_span`); a :class:`SourceSpanValidationError`
        is raised for invalid input. V1 callers that don't supply
        ``step_name`` cannot use this helper and should construct
        :class:`ExtractionChunk` directly.
        """
        validate_source_span(source_span)
        span_hash = compute_source_span_hash(job_id, step_name, source_span)

        existing: ExtractionChunk | None = (
            session.query(cls)
            .filter(
                cls.job_id == job_id,
                cls.step_name == step_name,
                cls.source_span_hash == span_hash,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

        chunk = cls(
            job_id=job_id,
            step_name=step_name,
            content=content,
            source_span=source_span,
            source_span_hash=span_hash,
            chunk_index=chunk_index,
            token_estimate=token_estimate,
            metadata_=metadata_,
            source_reference=source_reference,
        )
        session.add(chunk)
        return chunk

    def __repr__(self) -> str:
        return (
            f"<ExtractionChunk id={self.id!s} job_id={self.job_id!s} "
            f"step={self.step_name!r} index={self.chunk_index!r}>"
        )
