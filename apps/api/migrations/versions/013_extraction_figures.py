"""Create extraction_figures table (NFM-852)

Revision ID: 013
Revises: 012
Create Date: 2026-07-08

Creates the extraction_figures table for storing extracted
figures (plots, charts, diagrams) from literature sources.

Spec reference: section 6.1 (New tables: extraction_figures).

Note: uses data_sources FK instead of extraction_jobs because
extraction_jobs is currently an in-memory store, not a DB table.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create extraction_figures table with indexes."""

    op.execute("""
        CREATE TABLE extraction_figures (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
            page_number INTEGER NOT NULL,
            figure_type VARCHAR(50) NOT NULL,
            bounding_box JSONB,
            caption TEXT,
            image_path VARCHAR(500),
            extracted_data JSONB NOT NULL DEFAULT '{}',
            confidence FLOAT NOT NULL DEFAULT 0.0,
            extraction_method VARCHAR(50),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_extraction_figures_confidence
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CONSTRAINT ck_extraction_figures_page
                CHECK (page_number > 0)
        )
    """)

    # Indexes for common query patterns
    op.execute(
        "CREATE INDEX ix_extraction_figures_source_id "
        "ON extraction_figures (source_id)"
    )
    op.execute(
        "CREATE INDEX ix_extraction_figures_figure_type "
        "ON extraction_figures (figure_type)"
    )
    op.execute(
        "CREATE INDEX ix_extraction_figures_page_number "
        "ON extraction_figures (page_number)"
    )
    # GIN index on JSONB extracted_data for property queries
    op.execute(
        "CREATE INDEX ix_extraction_figures_extracted_data "
        "ON extraction_figures USING gin (extracted_data)"
    )


def downgrade() -> None:
    """Drop extraction_figures table."""
    op.execute("DROP TABLE IF EXISTS extraction_figures CASCADE")
