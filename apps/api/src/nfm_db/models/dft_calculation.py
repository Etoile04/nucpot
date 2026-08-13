"""DFT calculation record ORM model.

Stores first-principles DFT calculation parameters, energy results, and
structural properties.  One DFTCalculation links to zero-or-one Material via
material_id so that calculations performed on compositions not yet registered
as materials can still be recorded.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, CompatJSONB, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.material import Material


class DFTCalculation(TimestampMixin, Base):
    """A single DFT (Density Functional Theory) calculation record.

    Captures computation parameters (functional, cutoff energy, k-point mesh),
    energy results (formation energy, cohesive energy), and structural metrics
    (lattice distortion).  Designed to store VASP / Quantum ESPRESSO /
    CASTEP output metadata alongside computed properties.
    """

    __tablename__ = "dft_calculations"
    __table_args__ = (
        UniqueConstraint("calculation_id", name="uq_dft_calculations_calc_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_dft_calculations_status",
        ),
        Index("idx_dft_calcs_material", "material_id"),
        Index("idx_dft_calcs_calc_id", "calculation_id"),
        Index("idx_dft_calcs_status", "status"),
        Index("idx_dft_calcs_functional", "functional"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    calculation_id: Mapped[str] = mapped_column(
        String(200),
        comment="External calculation identifier (e.g. VASP job ID)",
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("materials.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to materials table; NULL if material not yet registered",
    )

    # -- Computation parameters --
    functional: Mapped[str] = mapped_column(
        String(50),
        comment="XC functional (PBE, PBEsol, LDA, HSE06, etc.)",
    )
    cutoff_energy: Mapped[float] = mapped_column(
        Numeric(10, 2),
        comment="Plane-wave cutoff energy in eV",
    )
    kpoint_mesh: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="K-point mesh string (e.g. '4x4x4', '8x8x8')",
    )
    kpoint_density: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="K-point density in k-points/A^-3",
    )
    convergence_criteria: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Energy convergence criterion (e.g. '1e-5 eV')",
    )
    exchange_correlation: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Exchange-correlation detail beyond functional name",
    )
    pseudopotential: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Pseudopotential library (PAW_PBE, USPP, etc.)",
    )
    spin_polarization: Mapped[bool | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Spin polarization settings (JSON: ispin, magmom, etc.)",
    )

    # -- Energy results --
    formation_energy: Mapped[float | None] = mapped_column(
        Numeric(16, 6),
        nullable=True,
        comment="Formation energy in eV/atom",
    )
    cohesive_energy: Mapped[float | None] = mapped_column(
        Numeric(16, 6),
        nullable=True,
        comment="Cohesive energy in eV/atom",
    )

    # -- Structural parameters --
    lattice_distortion: Mapped[float | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="Lattice distortion parameter delta",
    )

    # -- Metadata --
    source: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Data source tag (e.g. 'materials_project', 'incremental_200')",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
        comment="pending | running | completed | failed | cancelled",
    )
    computation_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "computation_metadata",
        CompatJSONB,
        nullable=True,
        comment="Arbitrary computation metadata (VASP version, INCAR params, etc.)",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # -- Relationships --
    material: Mapped[Material | None] = relationship(
        back_populates="dft_calculations",
    )

    def __repr__(self) -> str:
        return (
            f"<DFTCalculation id={self.id!s} "
            f"calc_id={self.calculation_id!r}>"
        )
