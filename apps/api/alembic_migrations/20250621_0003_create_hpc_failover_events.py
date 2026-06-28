"""Create hpc_failover_events table for NFM-346 Phase 4.5.

Revision ID: 0003
Revises: 0002
Create Date: 2025-06-21 14:30:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0003_create_hpc_failover_events'
down_revision = '0002_create_ref_gap_fill_staging'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create hpc_failover_events table and indexes."""
    op.create_table(
        'hpc_failover_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('source_cluster', sa.String(length=50), nullable=False),
        sa.Column('target_cluster', sa.String(length=50), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('success', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Create indexes for performance
    op.create_index(
        'idx_failover_events_time',
        'hpc_failover_events',
        ['event_time'],
        unique=False
    )

    op.create_index(
        'idx_failover_events_type',
        'hpc_failover_events',
        ['event_type'],
        unique=False
    )

    op.create_index(
        'idx_failover_events_cluster',
        'hpc_failover_events',
        ['source_cluster'],
        unique=False
    )


def downgrade() -> None:
    """Drop hpc_failover_events table and indexes."""
    op.drop_index('idx_failover_events_cluster', table_name='hpc_failover_events')
    op.drop_index('idx_failover_events_type', table_name='hpc_failover_events')
    op.drop_index('idx_failover_events_time', table_name='hpc_failover_events')
    op.drop_table('hpc_failover_events')
