"""Property, dataset, and measurement ORM models.

Phase 1 core tables: property_categories, property_types, datasets,
property_measurements, measurement_conditions.
Stores material property data with multi-type value support and conditions.
"""

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

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
    value_scalar: Mapped[float | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    value_min: Mapped[float | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    value_max: Mapped[float | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    value_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_list: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainty: Mapped[float | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    __table_args__ = (
        Index("idx_mc_measurement", "measurement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    measurement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property_measurements.id", ondelete="CASCADE"),
        index=True,
    )
    temperature: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )
    pressure: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )
    environment: Mapped[str | None] = mapped_column(String(200), nullable=True)
    irradiation_dose: Mapped[float | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    measurement: Mapped["PropertyMeasurement"] = relationship(
        back_populates="conditions",
    )

    def __repr__(self) -> str:
        return (
            f"<MeasurementCondition id={self.id!s} "
            f"measurement={self.measurement_id!s}>"
        )
