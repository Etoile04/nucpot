"""merge heads 011 and 026

Revision ID: 027
Revises: 011, 026
Create Date: 2026-07-27

Merges the two Alembic heads so `alembic upgrade head` resolves
to a single target.  011 (KG tables, NFM-838 Batch 2) forked from 010
and was never merged back into the main lineage (which runs through
012 → 013 → 020 → ... → 026).  The table objects created by 011 already
exist in the production database (applied manually during earlier
hot-patches), so this migration is a no-op — it only resolves the
head divergence.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "027"
down_revision: str | Sequence[str] | None = ("011", "026")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
