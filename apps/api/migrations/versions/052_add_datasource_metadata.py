"""Add DataSource.metadata_ JSONB column (NFM-2649)

The literature-fill path handler stores search keywords, request triples,
and placeholder markers in DataSource.metadata_ so the Celery literature
worker can pick up the search context when the actual search runs.

Existing rows are back-filled with NULL by Postgres automatically when the
column is added without a default.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "052_add_datasource_metadata"
down_revision: str | Sequence[str] | None = "051_extraction_job_orchestration_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the metadata_ JSONB column to data_sources."""
    op.execute(
        """
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS metadata_ JSONB
        """
    )


def downgrade() -> None:
    """Remove the metadata_ JSONB column from data_sources."""
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS metadata_")
