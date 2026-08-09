"""ExtractionJob ORM model.

Represents a single extraction pipeline run.  NFM-2013 extended the
stub into a real persistence target so operators can audit what landed
in the database and poll the new
``GET /api/v1/extraction/ingest/{job_id}/status`` endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin

# Status values mirror the contract documented for /ingest/{job_id}/status.
# Mapping is synchronous in the current handler, so the persisted rows
# always land at 'completed' (or 'failed' if map_and_persist raised).
EXTRACTION_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "completed",
    "failed",
)


class ExtractionJob(TimestampMixin, Base):
    """A single extraction pipeline run (NFM-2013 AC-2 + AC-5).

    The original stub carried only multimodal-extraction flags.  NFM-2013
    added provenance + status fields so the ingest handler can persist
    a row on every POST and the new status endpoint can serve the
    real state instead of the Celery/in-memory fallback facade.
    """

    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Provenance (NFM-2013 AC-2) ---
    source_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="DOI / URL / file path the batch was extracted from.",
    )
    source_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="doi | url | file | internal_id | datasource.",
    )
    corpus_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="External corpus slug the batch was tagged with.",
    )

    # --- Status (NFM-2013 AC-5) ---
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        comment=(
            "pending | processing | completed | failed — see "
            "EXTRACTION_JOB_STATUSES."
        ),
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last failure reason when status='failed'.",
    )

    # --- Counts (NFM-2013 AC-5 / OntoFuel handoff contract) ---
    total_received: Mapped[int] = mapped_column(default=0, nullable=False)
    created_measurements: Mapped[int] = mapped_column(default=0, nullable=False)
    reused_entities: Mapped[int] = mapped_column(default=0, nullable=False)
    skipped_duplicate_measurements: Mapped[int] = mapped_column(
        default=0, nullable=False
    )
    skipped_unknown_properties: Mapped[int] = mapped_column(
        default=0, nullable=False
    )
    skipped_duplicates: Mapped[int] = mapped_column(
        default=0, nullable=False,
        comment="Backward-compat alias: reused + skipped_dup + skipped_unknown.",
    )
    validation_errors: Mapped[int] = mapped_column(default=0, nullable=False)

    # --- Timestamps ---
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- Ontology version tracking (NFM-2638) ---
    ontology_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ontology_versions.id"),
        nullable=True,
        comment="FK to the OntologyVersion used for prompt generation.",
    )
    ontology_version_str: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Denormalized semver string, e.g. 1.2.0, for easy querying.",
    )

    # --- Multimodal extraction flags (preserved from the stub) ---
    extract_figures: Mapped[bool] = mapped_column(default=False)
    extract_tables: Mapped[bool] = mapped_column(default=False)
    confidence_threshold: Mapped[float] = mapped_column(default=0.5)
    figure_types: Mapped[list[str] | None] = mapped_column(
        CompatJSONB, default=None, nullable=True,
    )

    # --- Orchestration columns (NFM-2745 — Phase A of NFM-2739) ---
    # These columns mirror the dataclass ``ExtractionJob`` fields so the
    # ORM row can carry the full state the in-memory dataclass carries
    # today.  Per ADR-NFM-2739 §2.1 the canonical dict contract emits
    # all 10 on BOTH paths; the dataclass→ORM migration (Phase B, still
    # blocked) is what lets callers actually read these columns instead
    # of relying on the getattr fallbacks in ``_extraction_job_to_dict``.
    #
    # Defaults below MUST match the values
    # ``_extraction_job_to_dict`` already emits on the ORM path — see
    # ADR-NFM-2739 §2.1.  Changing one silently changes the 24-key
    # contract.  ``fill_batch_id`` is intentionally a ``String`` (not
    # ``Uuid``/``UUID``) because ``api/v4/extraction.py:161`` does
    # ``uuid.UUID(job.fill_batch_id)`` and the dict contract binds
    # ``fill_batch_id`` to ``str | None``.
    fill_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        comment="Dataclass fill_batch_id; stored as str for UUID-parsing compatibility.",
    )
    extracted_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Number of properties extracted from the source.",
    )
    staged_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Number of properties that passed quality gate and reached staging.",
    )
    rejected_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Number of properties rejected by the quality gate.",
    )
    element_systems: Mapped[list[str] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
        default=None,
        comment="Element-system filter list passed into the request.",
    )
    cache_level: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
        comment="Cache level override for property mapping (e.g. L1 / L2).",
    )
    max_confidence: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
        comment="Max-confidence cap the quality gate enforces.",
    )
    conflict_strategy: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="prefer_vlm",
        server_default="prefer_vlm",
        comment="Conflict resolution strategy when VLM and DB disagree.",
    )
    figures: Mapped[list[dict[str, Any]]] = mapped_column(
        CompatJSONB,
        nullable=False,
        default=list,
        server_default="[]",
        comment="Multimodal extraction figures captured for this job.",
    )
    tables: Mapped[list[dict[str, Any]]] = mapped_column(
        CompatJSONB,
        nullable=False,
        default=list,
        server_default="[]",
        comment="Multimodal extraction tables captured for this job.",
    )

    def __init__(self, **kwargs: Any) -> None:
        """Apply documented defaults so ``_extraction_job_to_dict`` round-trips.

        The dataclass ``ExtractionJob`` (NFM-2743) carries these 10
        orchestration fields with explicit defaults (``0`` for the
        counts, ``"prefer_vlm"`` for the conflict strategy, ``[]`` for
        ``figures``/``tables``, ``None`` for the optional request-side
        fields).  The canonical dict contract binds the ORM path to
        the same defaults, so transient ORM rows must yield those
        values when read.

        SQLAlchemy 2.0's auto-generated ``__init__`` does NOT apply
        ``Column.default`` at instance creation — it only fires at
        INSERT-flush time.  Without this override, a freshly-constructed
        ``ORMExtractionJob(...)`` returns ``None`` for every unset
        mapped attribute and the helper's ``getattr(job, name, 0)``
        fallback never fires (the descriptor returns ``None`` rather
        than raising ``AttributeError``).  That would silently change
        the 24-key contract to one where defaults become ``None``.

        Applying the defaults here is a no-op for callers that pass
        explicit values and matches the dataclass's source-of-truth
        defaults for callers that don't.
        """
        defaults: dict[str, Any] = {
            "extracted_count": 0,
            "staged_count": 0,
            "rejected_count": 0,
            "conflict_strategy": "prefer_vlm",
            "figures": [],
            "tables": [],
        }
        for key, value in defaults.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<ExtractionJob id={self.id!s} status={self.status!r} "
            f"source={self.source_reference!r}>"
        )
