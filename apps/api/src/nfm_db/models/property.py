"""Property, dataset, and measurement ORM models.

Phase 1 core tables: property_categories, property_types, datasets,
property_measurements, measurement_conditions.
Stores material property data with multi-type value support and conditions.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.material import Material
    from nfm_db.models.source import DataSource
    from nfm_db.models.unit import Unit


class PropertyCategory(TimestampMixin, Base):
    """High-level property category (thermal, mechanical, nuclear, etc.)."""

    __tablename__ = "property_categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_property_categories_name"),
        UniqueConstraint("slug", name="uq_property_categories_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    property_types: Mapped[list["PropertyType"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PropertyCategory id={self.id!s} name={self.name!r}>"


class PropertyType(TimestampMixin, Base):
    """A specific measurable property within a category."""

    __tablename__ = "property_types"
    __table_args__ = (
        UniqueConstraint(
            "category_id",
            "slug",
            name="uq_property_types_category_slug",
        ),
        Index("idx_property_types_category", "category_id"),
        CheckConstraint(
            "value_type IN ('scalar', 'range', 'expression', 'list', 'text')",
            name="ck_property_types_value_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property_categories.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200))
    value_type: Mapped[str] = mapped_column(String(50))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    category: Mapped["PropertyCategory"] = relationship(back_populates="property_types")
    default_unit: Mapped["Unit | None"] = relationship(
        back_populates="property_types",
    )
    measurements: Mapped[list["PropertyMeasurement"]] = relationship(
        back_populates="property_type",
    )

    def __repr__(self) -> str:
        return f"<PropertyType id={self.id!s} name={self.name!r}>"


class Dataset(TimestampMixin, Base):
    """A group of measurements from one material + one source."""

    __tablename__ = "datasets"
    __table_args__ = (
        Index("idx_datasets_material", "material_id"),
        Index("idx_datasets_source", "source_id"),
        # NFM-2032 / NFM-2013 AC-4: DB-enforced uniqueness on
        # (source, material) so the mapper's dataset-dedup lookup is
        # not a check-then-insert race.  The DB-level invariant catches
        # both the legacy duplicate-state reported in NFM-2009 and any
        # concurrent-rerun duplicate creation.
        UniqueConstraint(
            "source_id", "material_id", name="uq_datasets_source_material"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
        index=True,
    )
    # NFM-4159 — source_id may be NULL on the recast cohort (datasets that
    # survived migration 070's cascade with their FK nulled).  Allowed by
    # the §5.1 server filter; the ORM keeps a CASCADE on delete for the
    # non-null case so admin-side ``DELETE FROM data_sources WHERE id = ?``
    # still propagates.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # NFM-4549 / G1-C — literature ingestion dedup (ADR-017 §2.5).
    # ``literature_doi`` is the normalized DOI; partial-uniqueness is
    # enforced by migration 085 so legacy NULL rows still coexist.
    literature_doi: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment=(
            "Normalized DOI (URL/whitespace stripped, lowercased). "
            "Partial-unique: NULL allowed for legacy rows."
        ),
    )
    # ``literature_content_hash`` is the SHA-256 of the canonical PDF
    # bytes — fallback identity signal when no DOI is present.
    literature_content_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=("SHA-256 of the canonical PDF bytes (or normalized content_md)."),
    )

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="datasets")
    source: Mapped["DataSource"] = relationship(back_populates="datasets")
    measurements: Mapped[list["PropertyMeasurement"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Dataset id={self.id!s} title={self.title!r}>"


class PropertyMeasurement(TimestampMixin, Base):
    """A single property measurement with multi-type value support."""

    __tablename__ = "property_measurements"
    __table_args__ = (
        CheckConstraint(
            "value_scalar IS NOT NULL OR value_min IS NOT NULL "
            "OR value_max IS NOT NULL OR value_expression IS NOT NULL "
            "OR value_list IS NOT NULL OR value_text IS NOT NULL",
            name="ck_property_measurements_value_present",
        ),
        Index("idx_pm_dataset", "dataset_id"),
        Index("idx_pm_property_type", "property_type_id"),
        # NFM-4549 / G1-C — AC-9 dedupe_key UNIQUE constraint. The
        # ``dedupe_key`` column is a server-side composite of
        # (dataset_id, property_type_id, source_id, value_hash) —
        # the writer side is ``compute_dedupe_key`` in
        # ``nfm_db.services.literature_dedup``, called by
        # ``extraction_to_db_mapper.map_and_persist`` on every new
        # INSERT. Owen 2023's 92-row duplicate case (same value,
        # same source, same dataset) collapses to one row at the DB
        # level — the unique index turns the second INSERT into
        # IntegrityError. Legacy rows may be NULL; PG + SQLite both
        # permit multiple NULLs in a UNIQUE index by SQL spec.
        UniqueConstraint("dedupe_key", name="uq_property_measurements_dedupe_key"),
        # NFM-2032 / NFM-2013 AC-4: composite UNIQUE INDEX that enforces
        # the 5-tuple dedup key (NFM-1981 AC-2) at the DB level.
        # The in-memory set + check-then-INSERT was racy and omitted
        # ``method``, so a tensile test and a nanoindentation test on
        # the same conditions collapsed to one row.  This DB-level
        # constraint turns concurrent-rerun races into IntegrityError,
        # which the mapper catches and counts as
        # skipped_duplicate_measurements.
        UniqueConstraint(
            "dataset_id",
            "property_type_id",
            "conditions_hash",
            "method",
            name="uq_pm_dedup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        index=True,
    )
    property_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property_types.id", ondelete="CASCADE"),
        index=True,
    )
    # Per ADR-011 D7 (NFM-3920 / NFM-3921): scale raised from 10 → 15 to
    # preserve values like ``1.27e-9`` (15 fractional digits rendered).
    # The prior ``NUMERIC(20, 10)`` rounded ``1.27e-9`` to ``1.3e-9``
    # (~2.4% error) and truncated values below ``5e-11`` to zero.
    value_scalar: Mapped[float | None] = mapped_column(
        Numeric(20, 15),
        nullable=True,
    )
    value_min: Mapped[float | None] = mapped_column(
        Numeric(20, 15),
        nullable=True,
    )
    value_max: Mapped[float | None] = mapped_column(
        Numeric(20, 15),
        nullable=True,
    )
    value_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_list: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainty: Mapped[float | None] = mapped_column(
        Numeric(20, 15),
        nullable=True,
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
        comment="pending | approved | rejected | needs_revision | corrected",
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last review action",
    )
    conditions_hash: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment=(
            "SHA1 hash of measurement conditions for dedup "
            "(NFM-2032 5-tuple). Nullable in ORM for legacy rows; "
            "migration 032 backfills + sets NOT NULL."
        ),
    )
    method: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        server_default="",
        comment="Measurement method (NFM-2032 5-tuple dedup).",
    )

    # NFM-4549 / G1-C — property_measurements dedupe_key
    # (ADR-017 §2.6 / spec §3.1). Nullable in the ORM so that
    # legacy rows + lower-level admin scripts can insert rows
    # without knowing the dedup composite; the UNIQUE constraint
    # still enforces uniqueness on the rows the mapper populates.
    # The mapper (``extraction_to_db_mapper.map_and_persist`` —
    # NFM-4549 / G1-C) writes the ``dedupe_key`` on INSERT via
    # ``nfm_db.services.literature_dedup.compute_dedupe_key``.
    # AC-9: Owen 2023's 92-row case study (same value, same source,
    # same dataset) collapses to one row once the mapper calls
    # ``compute_dedupe_key`` — the unique index turns the second
    # INSERT into IntegrityError.
    dedupe_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "sha256(dataset|property|source|value_hash) per ADR-017 §2.6. "
            "NULL permitted only for legacy rows or admin scripts; the "
            "mapper MUST populate this on new ingestion rows."
        ),
    )

    # -- relationships --
    dataset: Mapped["Dataset"] = relationship(back_populates="measurements")
    property_type: Mapped["PropertyType"] = relationship(
        back_populates="measurements",
    )
    unit: Mapped["Unit | None"] = relationship(back_populates="measurements")
    conditions: Mapped[list["MeasurementCondition"]] = relationship(
        back_populates="measurement",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PropertyMeasurement id={self.id!s} dataset={self.dataset_id!s}>"


class MeasurementCondition(TimestampMixin, Base):
    """Experimental conditions for a measurement (T, P, environment, etc.)."""

    __tablename__ = "measurement_conditions"
    __table_args__ = (Index("idx_mc_measurement", "measurement_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    measurement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property_measurements.id", ondelete="CASCADE"),
        index=True,
    )
    temperature: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    pressure: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    environment: Mapped[str | None] = mapped_column(String(200), nullable=True)
    irradiation_dose: Mapped[float | None] = mapped_column(
        Numeric(16, 6),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    measurement: Mapped["PropertyMeasurement"] = relationship(
        back_populates="conditions",
    )

    def __repr__(self) -> str:
        return f"<MeasurementCondition id={self.id!s} measurement={self.measurement_id!s}>"
