"""Verification task ORM model (NFM-1750).

Lightweight model for LAMMPS verification tasks created from
Pareto recommendation compositions. Distinct from ``MDVerificationJob``
which tracks the full HPC pipeline lifecycle.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class TaskStatus(str, enum.Enum):
    """Verification task lifecycle status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationTask(TimestampMixin, Base):
    """LAMMPS verification task created from a Pareto recommendation.

    Stores the composition, potential function selection, temperature
    range, and timestep count. On completion, an A-F structural
    stability rating is stored alongside the raw simulation metrics.
    """

    __tablename__ = "verification_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Composition ---

    composition: Mapped[dict[str, float]] = mapped_column(
        JSON,
        nullable=False,
    )

    # --- Simulation configuration ---

    potential_function: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    temperature_min: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    temperature_max: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    timestep_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # --- Lifecycle ---

    status: Mapped[TaskStatus] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        default=TaskStatus.QUEUED,
    )
    rating: Mapped[str | None] = mapped_column(
        String(1),
        nullable=True,
    )
    rating_summary: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    rating_metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="check_verification_task_status",
        ),
        CheckConstraint(
            "rating IS NULL OR rating IN ('A', 'B', 'C', 'D', 'F')",
            name="check_verification_task_rating",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<VerificationTask id={self.id!s} "
            f"status={self.status.value} "
            f"rating={self.rating!r}>"
        )
