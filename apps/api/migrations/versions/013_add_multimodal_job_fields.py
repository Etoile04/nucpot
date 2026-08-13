"""Add multimodal extraction fields to extraction_jobs

Revision ID: 013
Revises: 012
Create Date: 2026-07-13 00:00:00.000000

NFM-1342 — Cherry-pick of NFM-979 minimal model+sig changes.

Adds four columns to the extraction_jobs table to support multimodal
(VLM-based) figure and table extraction:
- extract_figures (BOOLEAN, default False)
- extract_tables (BOOLEAN, default False)
- confidence_threshold (FLOAT, default 0.5)
- figure_types (JSONB, nullable)
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013"
down_revision: Union[str, None, Sequence[str]] = "012"
branch_labels: Union[str, None, Sequence[str]] = None
depends_on: Union[str, None, Sequence[str]] = None


def upgrade() -> None:
    """Add multimodal extraction columns to extraction_jobs."""
    op.add_column(
        "extraction_jobs",
        op.column("extract_figures", op.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "extraction_jobs",
        op.column("extract_tables", op.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "extraction_jobs",
        op.column("confidence_threshold", op.Float(), nullable=False, server_default="0.5"),
    )
    op.add_column(
        "extraction_jobs",
        op.column("figure_types", op.JSONB(astext_type=op.Text()), nullable=True),
    )


def downgrade() -> None:
    """Drop multimodal extraction columns from extraction_jobs."""
    op.drop_column("extraction_jobs", "figure_types")
    op.drop_column("extraction_jobs", "confidence_threshold")
    op.drop_column("extraction_jobs", "extract_tables")
    op.drop_column("extraction_jobs", "extract_figures")
