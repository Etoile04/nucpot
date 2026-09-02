"""Formal ``reference_values`` table model — NFM-3872 (Wayfinder pilot C / C-S1).

Per NFM-3830 Q1=C decision (Option A transition formalization): once a
``_ref_gap_fill_staging`` row passes the C-I1 admission gate (NFM-3871
DOI pre-screen + 30 % Crossref/OpenAlex statistical validation), it is
promoted into this ``reference_values`` table. The formal table is the
authoritative read source for the post-promotion fallback read path
(``ontology_service.derive_ontology_graph`` reads from here, not
staging, after C-S1 ships).

Schema rationale
----------------

Columns mirror the production ``reference_values`` table on Supabase
that the web matrix route reads (NFM-3780 fix — those are the actual
column names used in production: ``element``, ``crystal_structure``,
``property_name``, ``value``, ``unit``, ``source``, ``notes``). The
backend ``reference_values`` table in NFMD adds:

* ``staging_id`` (UUID, UNIQUE) — 1:1 link back to the originating
  ``_ref_gap_fill_staging`` row, used for idempotent re-runs and audit.
* DOI attribution (``source_doi``, ``uncertainty``, ``temperature``) —
  the DOI gate from C-I1 only makes sense if the admitted DOI survives
  the trip to the formal table, otherwise we re-introduce the very
  contamination vector C-I1 was created to block.
* ETL provenance (``etl_issue``, ``etl_manifest_ref``, ``etl_ok_reason``,
  ``promoted_at``) — when a future C-line run is reviewed, the operator
  needs to see which manifest admitted each row and why, without
  re-running C-I1.

Naming
------

* ``element`` (not ``element_system``) and ``crystal_structure`` (not
  ``phase``) match the Supabase production column names so the
  ETL → Supabase copy path is mechanical. The legacy staging names
  are mapped at ETL time inside ``promote_staging_etl``.

* ``notes`` is free-form TEXT for ETL provenance (e.g.
  ``promoted from staging via NFM-3872 manifest ...``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class ReferenceValue(TimestampMixin, Base):
    """Authoritative read-source for admitted reference values.

    Populated exclusively by ``promote_staging_etl.promote_admitted_rows``
    from ``_ref_gap_fill_staging`` rows that pass the C-I1 admission
    gate (NFM-3871). The relationship to staging is 1:1 keyed by
    ``staging_id`` (UNIQUE), which makes the ETL re-runnable without
    producing duplicate formal rows.
    """

    __tablename__ = "reference_values"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Provenance / 1:1 link to staging row ---
    #
    # NOTE: the column type is left at the SQLAlchemy default for
    # ``Mapped[uuid.UUID]`` (CHAR(32) on SQLite, PG UUID on
    # PostgreSQL via the dialect-aware dispatch). We deliberately do
    # NOT use ``PG_UUID(as_uuid=True)`` here — the FK target
    # ``_ref_gap_fill_staging.id`` is also the dialect-default UUID
    # type, so an explicit PG_UUID on this FK column produced a
    # ``staging_id UUID NOT NULL`` schema on SQLite (unhandled by
    # SQLite's type-affinity rules) which caused a FK type-mismatch
    # failure when the unit tests inserted rows on SQLite. The
    # migration's column type matches the ORM (no PG_UUID override).
    staging_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("_ref_gap_fill_staging.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment=(
            "1:1 link to the originating _ref_gap_fill_staging.id. UNIQUE "
            "is what makes the ETL re-runnable without duplicate writes."
        ),
    )

    # --- Domain columns (production schema names — NFM-3780) ---

    element: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "Element system identifier (e.g. 'U', 'UO2'). Maps from "
            "staging.element_system at ETL time."
        ),
    )
    crystal_structure: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "Crystal / thermodynamic phase (e.g. 'BCC', 'FCC'). Maps from "
            "staging.phase at ETL time."
        ),
    )
    property_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Property name (e.g. 'lattice_constant', 'bulk_modulus').",
    )
    value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Numeric property value.",
    )
    unit: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Measurement unit (e.g. 'angstrom', 'GPa').",
    )
    method: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Measurement / calculation method (e.g. 'DFT', 'EXP').",
    )
    source: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment=(
            "Data source identifier (e.g. 'Owen2023'). Same as "
            "staging.source — used as the corpus_id fallback read key."
        ),
    )
    source_doi: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment=(
            "DOI of the source publication. Only populated when the C-I1 "
            "admission gate admitted the row; rows admitted on prescreen "
            "alone (unsampled 70 %) still carry the DOI verbatim."
        ),
    )
    uncertainty: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Measurement uncertainty.",
    )
    temperature: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Measurement temperature in Kelvin.",
    )

    # --- Notes (free-form) ---
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Free-form notes column matching production. Populated by the "
            "ETL with provenance text so a future operator can trace each "
            "row back to its admission decision without rerunning C-I1."
        ),
    )

    # --- ETL provenance (audit trail) ---

    etl_issue: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment=(
            "Paperclip issue ID that ran the promotion (e.g. 'NFM-3872'). "
            "Used for incident review / replay."
        ),
    )
    etl_manifest_ref: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Path or identifier of the C-I1 manifest that admitted this "
            "row. May be a local path or an S3 URL."
        ),
    )
    etl_ok_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Short token explaining why C-I1 admitted this row. One of: "
            "'prescreen_pass', 'prescreen_pass+sampled_validated', "
            "'prescreen_pass+sampled_dry_run'. Used by operators to "
            "triage admission quality without rerunning the gate."
        ),
    )
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "Server-side INSERT timestamp. Distinct from created_at so a "
            "future re-promotion / refresh can update created_at without "
            "losing the original admission timestamp."
        ),
    )

    # --- Indexes ---
    #
    # The fallback read path (derive_ontology_graph) filters by source
    # (= corpus_id). The element+property_name index supports the
    # materials view that resolves record_ref deep links from the
    # ontology viewer.
    __table_args__ = (
        Index("idx_rv_source", "source"),
        Index(
            "idx_rv_element_property",
            "element",
            "property_name",
        ),
        # staging_id is already covered by the UNIQUE constraint, but the
        # explicit index makes reverse lookups during ETL idempotency
        # explicit in the schema.
        UniqueConstraint(
            "staging_id",
            name="uq_rv_staging_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ReferenceValue id={self.id!s} "
            f"element={self.element!r} "
            f"prop={self.property_name!r} "
            f"source={self.source!r} "
            f"value={self.value:g} {self.unit!r}>"
        )


__all__ = ["ReferenceValue"]
