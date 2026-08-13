"""merge_dft_and_datasource_branches

Revision ID: f8e2db803b55
Revises: 021, 023
Create Date: 2026-07-20 02:29:07.031526

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8e2db803b55'
down_revision: Union[str, Sequence[str], None] = ('021', '023')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
