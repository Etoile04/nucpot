"""Add source_id + page_number + extracted_data + confidence columns to extraction_figures.

Revision ID: 026
Revises: 025
Create Date: 2026-07-27

Fix: the production extraction_figures table only has (id, job_id, file_path)
from migration 014's stub block. Migration 013 was meant to add the full
schema (source_id FK to data_sources, page_number, figure_type, extracted_data,
confidence, etc.), but its revision chain is broken (revision="016" while
down_revision="020") so it never ran.

This migration closes the gap so that LLM-driven figure extraction can
populate figure metadata linked back to the source literature.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str | Sequence[str] | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add missing columns to extraction_figures."""
    # All ops use IF NOT EXISTS so the migration is idempotent.
    op.execute(
        "ALTER TABLE extraction_figures "
        "ADD COLUMN IF NOT EXISTS source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS page_number INTEGER")
    op.execute("ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS figure_type VARCHAR(50)")
    op.execute("ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS bounding_box JSONB")
    op.execute("ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS caption TEXT")
    op.execute("ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)")
    op.execute(
        "ALTER TABLE extraction_figures "
        "ADD COLUMN IF NOT EXISTS extracted_data JSONB NOT NULL DEFAULT '{}'"
    )
    op.execute(
        "ALTER TABLE extraction_figures "
        "ADD COLUMN IF NOT EXISTS confidence FLOAT NOT NULL DEFAULT 0.0"
    )
    op.execute(
        "ALTER TABLE extraction_figures "
        "ADD COLUMN IF NOT EXISTS extraction_method VARCHAR(50)"
    )
    op.execute(
        "ALTER TABLE extraction_figures "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )

    # Indexes (also idempotent)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_extraction_figures_source_id "
        "ON extraction_figures (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_extraction_figures_figure_type "
        "ON extraction_figures (figure_type)"
    )


def downgrade() -> None:
    """Drop columns added by this migration."""
    op.execute("DROP INDEX IF EXISTS ix_extraction_figures_source_id")
    op.execute("DROP INDEX IF EXISTS ix_extraction_figures_figure_type")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS extraction_method")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS confidence")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS extracted_data")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS image_path")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS caption")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS bounding_box")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS figure_type")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS page_number")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS source_id")
    op.execute("ALTER TABLE extraction_figures DROP COLUMN IF EXISTS updated_at")
