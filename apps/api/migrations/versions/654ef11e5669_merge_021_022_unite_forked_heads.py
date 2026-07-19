"""merge_021_022_unite_forked_heads

Revision ID: 654ef11e5669
Revises: 021, 022
Create Date: 2026-07-19 18:26:58.685400

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '654ef11e5669'
down_revision: Union[str, Sequence[str], None] = ('021', '022')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
