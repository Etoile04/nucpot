"""merge datasource storage and review traceability branches

Revision ID: f09546037832
Revises: 021, 022
Create Date: 2026-07-19 19:18:27.825571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f09546037832'
down_revision: Union[str, Sequence[str], None] = ('021', '022')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
