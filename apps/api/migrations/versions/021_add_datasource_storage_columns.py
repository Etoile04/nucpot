"""Add DataSource storage + parse-status columns (NFM-1486)

Adds the storage-pipeline columns that the PDF upload + DOI fetcher feature
depends on, and back-fills parse_status for existing rows.

Revision ID: 021
Revises: 019
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "021"
down_revision: str | Sequence[str] | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add storage + parse-status columns to data_sources.

    Existing rows are back-filled with ``parse_status='uploaded'`` via the
    column server default, which Postgres applies automatically when the
    column is added with NOT NULL.
    """
    # file_path — relative storage path returned by LocalDiskStorage.save()
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS file_path VARCHAR(1000)
    """)

    # file_hash — sha256 hex digest, for dedup
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64)
    """)

    # file_size — bytes
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS file_size INTEGER
    """)

    # content_md — PDF → Markdown full text
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS content_md TEXT
    """)

    # parse_status — NOT NULL with default 'uploaded' back-fills existing rows.
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS parse_status VARCHAR(32)
                NOT NULL DEFAULT 'uploaded'
    """)

    # parse_error — free-form error message from the parse pipeline
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS parse_error TEXT
    """)

    # original_filename — user-supplied filename, retained for audit / UX
    op.execute("""
        ALTER TABLE data_sources
            ADD COLUMN IF NOT EXISTS original_filename VARCHAR(500)
    """)

    # Useful index for the pipeline worker (find rows that still need parsing).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_data_sources_parse_status ON data_sources (parse_status)"
    )


def downgrade() -> None:
    """Remove the storage + parse-status columns from data_sources."""
    op.execute("DROP INDEX IF EXISTS ix_data_sources_parse_status")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS original_filename")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS parse_error")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS parse_status")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS content_md")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS file_size")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS file_hash")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS file_path")
