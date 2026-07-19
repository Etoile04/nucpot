"""merge_heads_021_022

Revision ID: 9c3bd4e3c5cf
Revises: 021, 022
Create Date: 2026-07-19 19:37:44.420370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c3bd4e3c5cf'
down_revision: Union[str, Sequence[str], None] = ('021', '022')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
