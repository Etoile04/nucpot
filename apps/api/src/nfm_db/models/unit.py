"""Unit and unit conversion ORM models.

Phase 1 core tables: units, unit_conversions.
Supports unit definitions with conversion factors between units.
"""

import uuid

from sqlalchemy import (
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nfm_db.models.property import PropertyMeasurement, PropertyType


class Unit(TimestampMixin, Base):
    """A unit of measurement (Kelvin, Pascal, Joule, etc.)."""

    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("name", name="uq_units_name"),
        UniqueConstraint("symbol", name="uq_units_symbol"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(20))
    dimension: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- relationships --
    conversions_from: Mapped[list["UnitConversion"]] = relationship(
        back_populates="source_unit",
        foreign_keys="UnitConversion.source_unit_id",
    )
    conversions_to: Mapped[list["UnitConversion"]] = relationship(
        back_populates="target_unit",
        foreign_keys="UnitConversion.target_unit_id",
    )
    property_types: Mapped[list["PropertyType"]] = relationship(
        back_populates="default_unit",
    )
    measurements: Mapped[list["PropertyMeasurement"]] = relationship(
        back_populates="unit",
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
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
    )
    target_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
    )
    factor: Mapped[float] = mapped_column(Numeric(20, 10))
    offset: Mapped[float] = mapped_column(Numeric(20, 10), default=0.0)

    # -- relationships --
    source_unit: Mapped["Unit"] = relationship(
        back_populates="conversions_from",
        foreign_keys=[source_unit_id],
    )
    target_unit: Mapped["Unit"] = relationship(
        back_populates="conversions_to",
        foreign_keys=[target_unit_id],
    )

    def __repr__(self) -> str:
        return (
            f"<UnitConversion id={self.id!s} "
            f"{self.source_unit.symbol!r}→{self.target_unit.symbol!r}>"
        )
