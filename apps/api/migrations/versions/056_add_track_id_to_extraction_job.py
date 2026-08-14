"""Add track_id column to extraction_jobs.

NFM-2881 AC-4: stores the LightRAG ingest tracking ID so operators
can poll ingest status per-job.

Revision ID: 056_add_track_id_to_extraction_job
Revises: 053_align_extraction_gap_with_adr_nfm_2675
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "056_add_track_id_to_extraction_job"
down_revision: str | Sequence[str] | None = (
    "055_add_ontology_version_id_to_type_tables"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable track_id column to extraction_jobs."""
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "track_id",
            sa.String(255),
            nullable=True,
            comment="LightRAG ingest tracking ID for status polling.",
        ),
    )


def downgrade() -> None:
    """Remove track_id column from extraction_jobs."""
    op.drop_column("extraction_jobs", "track_id")
