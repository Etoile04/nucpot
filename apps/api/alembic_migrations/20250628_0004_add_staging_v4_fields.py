"""Add 5 nullable v4 output fields to _ref_gap_fill_staging table.

Per CTO evaluation §3.1 (NFM-518): adds source_file, composition,
element, property_category, and context columns for v4 output support.

Revision ID: 0004
Revises: 0003
Create Date: 2025-06-28

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0004_add_staging_v4_fields'
down_revision = '0003_create_hpc_failover_events'
branch_labels = None
depends_on = None

TABLE = '_ref_gap_fill_staging'

NEW_COLUMNS = [
    ('source_file', sa.Text(), {}),
    ('composition', sa.Text(), {}),
    ('element', sa.Text(), {}),
    ('property_category', sa.String(length=50), {}),
    ('context', sa.Text(), {}),
]


def upgrade() -> None:
    """Add 5 nullable v4 output columns to staging table."""
    for col_name, col_type, col_kwargs in NEW_COLUMNS:
        op.add_column(TABLE, sa.Column(col_name, col_type, nullable=True, **col_kwargs))


def downgrade() -> None:
    """Remove 5 v4 output columns from staging table."""
    for col_name, _, _ in NEW_COLUMNS:
        op.drop_column(TABLE, col_name)
