"""merge 011 and 012 fork

Revision ID: 020
Revises: 011, 012
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "020"
down_revision: str | Sequence[str] | None = ("011", "015")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
