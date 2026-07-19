"""merge datasource and review migration branches

Revision ID: b8fd0260410f
Revises: 021, 022
Create Date: 2026-07-19 18:50:43.493620

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8fd0260410f'
down_revision: Union[str, Sequence[str], None] = ('021', '022')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
