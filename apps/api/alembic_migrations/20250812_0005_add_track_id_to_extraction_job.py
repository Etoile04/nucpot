"""Add track_id column to extraction_jobs table (NFM-2881).

Adds a nullable VARCHAR(255) column to persist the LightRAG ingest
tracking ID returned by ``LightRAGProvider.ingest()``.  The column
is nullable so existing rows remain valid (AC-3 backward compatible).

Revision ID: 0005
Revises: 0004
Create Date: 2025-08-12

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0005_add_track_id_to_extraction_job'
down_revision = '0004_add_staging_v4_fields'
branch_labels = None
depends_on = None

TABLE = 'extraction_jobs'
COLUMN = 'track_id'


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            sa.String(255),
            nullable=True,
            comment='LightRAG ingest tracking ID for status polling.',
        ),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
