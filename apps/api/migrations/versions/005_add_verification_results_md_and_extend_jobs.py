"""Add verification_results_md table and extend md_verification_jobs.

NFM-373 / NFM-369.3: Data model extension for MD cascade simulation results.

Creates:
- verification_results_md: Stores PKA cascade defect analysis metrics
  (vacancies, interstitials, frenkel_pairs, displaced_atoms, replaced_atoms,
  arc_dpa fitting, r_squared, sample_size, raw_dump_ref)

Extends:
- md_verification_jobs: Adds job_type, hpc_job_id, hpc_backend,
  execution_status columns for MD runner integration

Revision ID: 005
Revises: 004
Create Date: 2026-06-23

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: str | Sequence[str] | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add verification_results_md table and extend md_verification_jobs."""

    # --- Extend md_verification_jobs with new columns ---
    op.add_column(
        "md_verification_jobs",
        sa.Column(
            "job_type",
            sa.String(50),
            nullable=False,
            server_default="lookup",
        ),
    )
    op.add_column(
        "md_verification_jobs",
        sa.Column(
            "hpc_job_id",
            sa.String(255),
            nullable=True,
        ),
    )
    op.add_column(
        "md_verification_jobs",
        sa.Column(
            "hpc_backend",
            sa.String(100),
            nullable=True,
        ),
    )
    op.add_column(
        "md_verification_jobs",
        sa.Column(
            "execution_status",
            sa.String(50),
            nullable=True,
        ),
    )

    # --- Create verification_results_md table ---
    op.create_table(
        "verification_results_md",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "simulation_result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("md_simulation_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Defect counts
        sa.Column("vacancies", sa.Integer(), nullable=False),
        sa.Column("interstitials", sa.Integer(), nullable=False),
        sa.Column("frenkel_pairs", sa.Integer(), nullable=False),
        sa.Column("displaced_atoms", sa.Integer(), nullable=False),
        sa.Column("replaced_atoms", sa.Integer(), nullable=False),
        # arc-DPA fitting metrics
        sa.Column("arc_dpa_b", sa.Float(), nullable=True),
        sa.Column("arc_dpa_c", sa.Float(), nullable=True),
        sa.Column("r_squared", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        # Raw data reference
        sa.Column("raw_dump_ref", sa.Text(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # --- Create indexes ---
    op.create_index(
        "idx_vrmd_simulation_result_id",
        "verification_results_md",
        ["simulation_result_id"],
        unique=False,
    )
    op.create_index(
        "idx_md_jobs_job_type",
        "md_verification_jobs",
        ["job_type"],
        unique=False,
    )
    op.create_index(
        "idx_md_jobs_execution_status",
        "md_verification_jobs",
        ["execution_status"],
        unique=False,
    )


def downgrade() -> None:
    """Remove verification_results_md table and revert md_verification_jobs."""
    # Drop indexes
    op.drop_index(
        "idx_md_jobs_execution_status",
        table_name="md_verification_jobs",
    )
    op.drop_index(
        "idx_md_jobs_job_type",
        table_name="md_verification_jobs",
    )
    op.drop_index(
        "idx_vrmd_simulation_result_id",
        table_name="verification_results_md",
    )

    # Drop verification_results_md table
    op.drop_table("verification_results_md")

    # Remove columns from md_verification_jobs
    op.drop_column("md_verification_jobs", "execution_status")
    op.drop_column("md_verification_jobs", "hpc_backend")
    op.drop_column("md_verification_jobs", "hpc_job_id")
    op.drop_column("md_verification_jobs", "job_type")
