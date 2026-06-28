"""merge blog lineage (002) and feedback lineage (b5f3a2c1d8e0)

Revision ID: 9c15710c6321
Revises: 002, b5f3a2c1d8e0
Create Date: 2026-06-15 04:45:40.633605

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '9c15710c6321'
down_revision: str | Sequence[str] | None = ('002', 'b5f3a2c1d8e0')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
