"""MD verification ORM models for LAMMPS integration.

Phase 2.2 of NFM-313: MD verification backend integration.
Provides SQLAlchemy ORM models for the 5-table MD verification schema.
"""

import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class JobStatus(str, enum.Enum):
    """MD verification job status."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HpcJobStatus(str, enum.Enum):
    """HPC cluster job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DefectType(str, enum.Enum):
    """Defect type for defect analysis results."""

    VACANCY = "vacancy"
    INTERSTITIAL = "interstitial"
    DISLOCATION = "dislocation"
    GRAIN_BOUNDARY = "grain_boundary"
    OTHER = "other"


class FittingMethod(str, enum.Enum):
    """Potential fitting method."""

    ARC_DPA = "arc-dpa"
    RPA = "RPA"
    OTHER = "other"


class MDVerificationJob(TimestampMixin, Base):
    """Main MD verification job tracking.

    Tracks the lifecycle of a potential function verification job
    through MD simulation, defect analysis, and potential fitting.
    """

    __tablename__ = "md_verification_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Identification ---

    potential_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    element_system: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    phase: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # --- Configuration ---

    config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    # --- Job lifecycle ---

    status: Mapped[JobStatus] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        default=JobStatus.PENDING,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )
    submitted_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    started_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    completed_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )

    # --- Error handling ---

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'submitted', 'running', 'completed', 'failed')",
            name="check_md_job_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MDVerificationJob id={self.id!s} "
            f"potential={self.potential_id!r} "
            f"element={self.element_system!r} "
            f"status={self.status.value}>"
        )


class HpcJob(TimestampMixin, Base):
    """HPC cluster job execution details.

    Tracks submission and execution details for jobs sent to HPC clusters.
    """

    __tablename__ = "hpc_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Relationship ---

    verification_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("md_verification_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- HPC identification ---

    hpc_cluster: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    hpc_job_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # --- Job lifecycle ---

    status: Mapped[HpcJobStatus] = mapped_column(
        String(50),
        nullable=False,
        default=HpcJobStatus.PENDING,
    )

    # --- Resource allocation ---

    partition: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    nodes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # --- Time tracking ---

    walltime_requested: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    walltime_used: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    submitted_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    started_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    completed_at: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )

    # --- Output capture ---

    submission_output: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    submission_errors: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<HpcJob id={self.id!s} "
            f"cluster={self.hpc_cluster!r} "
            f"job_id={self.hpc_job_id!r} "
            f"status={self.status.value}>"
        )


class MDSimulationResult(TimestampMixin, Base):
    """MD simulation output data.

    Stores results from LAMMPS MD simulations including
    thermodynamic data and final system state.
    """

    __tablename__ = "md_simulation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Relationship ---

    verification_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("md_verification_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # --- Output files ---

    trajectory_file_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # --- Thermodynamic data ---

    thermodynamic_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # --- Simulation metrics ---

    simulation_time_ps: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    steps_completed: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # --- Final state ---

    final_energy: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    final_temperature: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    final_pressure: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<MDSimulationResult id={self.id!s} "
            f"job_id={self.verification_job_id!s} "
            f"steps={self.steps_completed}>"
        )


class DefectAnalysisResult(TimestampMixin, Base):
    """OVITO defect analysis results.

    Stores defect analysis results including defect types,
    concentrations, and formation energies.
    """

    __tablename__ = "defect_analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Relationship ---

    verification_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("md_verification_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Defect data ---

    defect_type: Mapped[DefectType] = mapped_column(
        String(100),
        nullable=False,
    )
    concentration: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    formation_energy: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # --- Additional metadata ---

    analysis_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint(
            "defect_type IN ('vacancy', 'interstitial', 'dislocation', 'grain_boundary', 'other')",
            name="check_defect_type",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DefectAnalysisResult id={self.id!s} "
            f"type={self.defect_type.value!r} "
            f"concentration={self.concentration}>"
        )


class PotentialFittingResult(TimestampMixin, Base):
    """arc-dpa/RPA fitting results.

    Stores potential fitting results including fitted parameters
    and quality metrics.
    """

    __tablename__ = "potential_fitting_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Relationship ---

    verification_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("md_verification_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Fitting data ---

    fitting_method: Mapped[FittingMethod] = mapped_column(
        String(50),
        nullable=False,
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    quality_metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint(
            "fitting_method IN ('arc-dpa', 'RPA', 'other')",
            name="check_fitting_method",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PotentialFittingResult id={self.id!s} "
            f"method={self.fitting_method.value!r}>"
        )


# --- Indexes ---

# Note: Indexes are created in Alembic migration 003_create_md_verification_tables.py
# and are documented here for reference:

# md_verification_jobs:
#   - idx_md_jobs_status (status)
#   - idx_md_jobs_potential (potential_id)

# hpc_jobs:
#   - idx_hpc_jobs_verification (verification_job_id)
#   - idx_hpc_jobs_cluster (hpc_cluster)

# defect_analysis_results:
#   - idx_defect_results_job (verification_job_id)

# potential_fitting_results:
#   - idx_fitting_results_job (verification_job_id)
