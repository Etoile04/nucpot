"""Unit and unit conversion ORM models.

Phase 1 core tables: units, unit_conversions.
Defines physical measurement units and conversion factors between them.
"""

import uuid

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nfm_db.models.property import PropertyMeasurement, PropertyType


class Unit(TimestampMixin, Base):
    """A physical measurement unit (e.g., K, MPa, J/mol, nm)."""

    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_units_symbol"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    symbol: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    property_types: Mapped[list["PropertyType"]] = relationship(
        back_populates="default_unit",
    )
    measurements: Mapped[list["PropertyMeasurement"]] = relationship(
        back_populates="unit",
    )
    conversions_as_source: Mapped[list["UnitConversion"]] = relationship(
        back_populates="source_unit",
        foreign_keys="[UnitConversion.source_unit_id]",
    )
    conversions_as_target: Mapped[list["UnitConversion"]] = relationship(
        back_populates="target_unit",
        foreign_keys="[UnitConversion.target_unit_id]",
    )

    def __repr__(self) -> str:
        return f"<Unit id={self.id!s} symbol={self.symbol!r}>"


class UnitConversion(TimestampMixin, Base):
    """Conversion factor between two units."""

    __tablename__ = "unit_conversions"
    __table_args__ = (
        UniqueConstraint(
            "source_unit_id",
            "target_unit_id",
            name="uq_unit_conversions_source_target",
        ),
        Index("idx_unit_conv_source", "source_unit_id"),
        Index("idx_unit_conv_target", "target_unit_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
        index=True,
    )
    target_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
        index=True,
    )
    factor: Mapped[float] = mapped_column(Numeric(16, 6))
    offset: Mapped[float] = mapped_column(Numeric(16, 6), default=0)

    # -- relationships --
    source_unit: Mapped["Unit"] = relationship(
        back_populates="conversions_as_source",
        foreign_keys=[source_unit_id],
    )
    target_unit: Mapped["Unit"] = relationship(
        back_populates="conversions_as_target",
        foreign_keys=[target_unit_id],
    )

    def __repr__(self) -> str:
        return (
            f"<UnitConversion id={self.id!s} "
            f"source={self.source_unit_id!s} target={self.target_unit_id!s}>"
        )
