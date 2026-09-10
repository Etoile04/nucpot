"""Property, dataset, and measurement ORM models.

Phase 1 core tables: property_categories, property_types, datasets,
property_measurements, measurement_conditions.
Stores material property data with multi-type value support and conditions.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, CompatJSONB, TimestampMixin

# ADR-017 §2.3 — dataset snapshot lifecycle. Mirrored by the
# ``ck_dataset_versions_status`` CHECK constraint in migration 085.
DATASET_VERSION_STATUSES = ("draft", "released", "rolled_back", "superseded")


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
        # NFM-4548 (G1-B) — ADR-017 §2.5 literature identity. Partial-unique
        # because most legacy datasets carry no DOI and their NULLs must not
        # collide with one another.
        Index(
            "uq_datasets_literature_doi",
            "literature_doi",
            unique=True,
            postgresql_where=text("literature_doi IS NOT NULL"),
        ),
        Index(
            "idx_datasets_literature_content_hash",
            "literature_content_hash",
            postgresql_where=text("literature_content_hash IS NOT NULL"),
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
    # -- NFM-4548 (G1-B) — ADR-017 §2.5 literature identity --
    literature_doi: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Normalised DOI — primary literature identity (ADR-017 §2.5).",
    )
    literature_content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="PDF content hash — identity fallback when DOI is absent.",
    )

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="datasets")
    source: Mapped["DataSource"] = relationship(back_populates="datasets")
    measurements: Mapped[list["PropertyMeasurement"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )
    versions: Mapped[list["DatasetVersion"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Dataset id={self.id!s} title={self.title!r}>"


class DatasetVersion(TimestampMixin, Base):
    """Snapshot-style dataset version (NFM-4548 / ADR-017 §2.3, AC-6).

    A version is a *snapshot of the mergeable set*, not a per-row delta:
    ``row_ids`` and ``source_ids`` record exactly which measurements and
    sources the snapshot captured.  Rollback is therefore a pointer switch
    rather than hand-written SQL — either whole-version (re-point
    ``released`` at any historical version) or by-source (snapshot a copy
    with one source's rows removed, then re-point).
    """

    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint(
            " OR ".join(
                f"status = '{value}'" for value in DATASET_VERSION_STATUSES
            ),
            name="ck_dataset_versions_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_dataset_versions_version_no"),
        UniqueConstraint(
            "dataset_id",
            "version_no",
            name="uq_dataset_versions_dataset_version_no",
        ),
        Index("idx_dataset_versions_dataset", "dataset_id"),
        Index("idx_dataset_versions_status", "dataset_id", "status"),
        # ADR-017 §2.3 rollback is a pointer switch, so a dataset has at
        # most one released version at any instant. Enforced in the DB so a
        # failed switch cannot leave two versions serving the public API.
        Index(
            "uq_dataset_versions_one_released",
            "dataset_id",
            unique=True,
            postgresql_where=text("status = 'released'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
    )
    version_no: Mapped[int] = mapped_column(
        Integer,
        comment="Monotonic per dataset, starting at 1.",
    )
    row_ids: Mapped[list[str]] = mapped_column(
        CompatJSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
        comment="property_measurements.id list captured by this snapshot.",
    )
    source_ids: Mapped[list[str]] = mapped_column(
        CompatJSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
        comment="data_sources.id list contributing to this snapshot.",
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="draft",
        default="draft",
        comment="draft | released | rolled_back | superseded (ADR-017 §2.3).",
    )

    # -- relationships --
    dataset: Mapped["Dataset"] = relationship(back_populates="versions")

    def __repr__(self) -> str:
        return (
            f"<DatasetVersion id={self.id!s} dataset={self.dataset_id!s} "
            f"v{self.version_no} {self.status}>"
        )



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
        # NFM-4548 (G1-B) — ADR-017 §2.6. Closes the hole uq_pm_dedup above
        # leaks: that index is a plain btree UNIQUE over a *nullable*
        # ``conditions_hash``, and Postgres treats NULLs as mutually
        # distinct, so identical rows with a NULL hash slip through it.
        # ``dedupe_key`` coalesces before hashing, so they collide.
        # Partial by design: legacy rows (no dataset_version_id) are
        # retained un-deduped per ADR-017 §4.1.
        Index(
            "uq_pm_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dataset_version_id IS NOT NULL"),
        ),
        Index(
            "idx_pm_conditions_gin",
            "conditions",
            postgresql_using="gin",
        ),
        Index(
            "idx_pm_simulation_method",
            "simulation_method",
            postgresql_where=text("simulation_method IS NOT NULL"),
        ),
        Index(
            "idx_pm_dataset_version",
            "dataset_version_id",
            postgresql_where=text("dataset_version_id IS NOT NULL"),
        ),
        Index("idx_pm_source", "source_id"),
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

    # -- NFM-4548 (G1-B) — ADR-016 §2.4 / ADR-017 §2.5 / spec §3.1 --
    #
    # NOTE ON THE NAME: the DB column is ``conditions`` (as the spec and
    # ADR require), but the Python attribute is ``conditions_data`` because
    # ``PropertyMeasurement.conditions`` is already taken by the legacy
    # one-to-many relationship below, which ``property_service.py`` still
    # selectinloads into the public API response.  Renaming the
    # relationship would change that response shape, so the two coexist for
    # the ADR-017 §4.2 dual-read period; the rename belongs to the ticket
    # that migrates the read path onto the JSONB bag.
    conditions_data: Mapped[dict[str, Any]] = mapped_column(
        "conditions",
        CompatJSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
        comment=(
            "Open measurement conditions (ADR-016 §2.4). High-frequency "
            "keys are promoted to sibling columns; every other key — "
            "including 'phase' per ADR-017 §2.5 — lives here."
        ),
    )
    simulation_method: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    temp_k: Mapped[float | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
        comment="Measurement temperature in Kelvin (spec §3.1 temp_K).",
    )
    pressure_gpa: Mapped[float | None] = mapped_column(
        Numeric(14, 6),
        nullable=True,
        comment="Measurement pressure in GPa (spec §3.1 pressure_GPa).",
    )
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Owning dataset_versions snapshot (spec §3.1). NULL marks a "
            "legacy row predating ADR-017 versioning; such rows are "
            "retained un-deduped per ADR-017 §4.1."
        ),
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        comment="data_sources.id — denormalised from datasets.source_id.",
    )
    # Both are PostgreSQL GENERATED ALWAYS ... STORED columns (see migration
    # 085). They are read-only by construction: any attempt to write one
    # raises at the DB. Declared here so the ORM can read them and so
    # ``Base.metadata`` does not drift from the live schema.
    value_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        server_default=FetchedValue(),
        server_onupdate=FetchedValue(),
        comment="GENERATED — md5 of the value columns + unit_id.",
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        server_default=FetchedValue(),
        server_onupdate=FetchedValue(),
        comment=(
            "GENERATED — ADR-017 §2.6 composite dedupe key. Unique across "
            "rows carrying a dataset_version_id (uq_pm_dedupe_key)."
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
