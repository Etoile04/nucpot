"""add_source_to_dft_calculations

Revision ID: 054b39a26310
Revises: f8e2db803b55
Create Date: 2026-07-21 08:58:13.851880

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '054b39a26310'
down_revision: Union[str, Sequence[str], None] = 'f8e2db803b55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'dft_calculations',
        sa.Column('source', sa.String(100), nullable=True, comment='Data source tag (e.g. materials_project, incremental_200)')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('dft_calculations', 'source')