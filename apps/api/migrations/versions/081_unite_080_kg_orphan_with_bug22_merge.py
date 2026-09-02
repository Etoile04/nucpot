"""unite 080_kg_orphan_bridge with 8f3a1c2d4e5f merge branch

Revision ID: 9a7c4d1f3b2e
Revises: 080_kg_orphan_bridge_u10mo_u3si_puo2, 8f3a1c2d4e5f
Create Date: 2026-09-03

NFM-167: NFM-4185 (#1122) added 080_kg_orphan_bridge_u10mo_u3si_puo2 to
main; this branch's 8f3a1c2d4e5f merge rev (uniting 079 with the prior
bug22 branch 40782ad79e2e) is now forked from 080. Unite them so the
graph returns to a single head.
"""

from typing import Sequence

from alembic import op

revision: str = "9a7c4d1f3b2e"
down_revision: str | Sequence[str] | None = ("080_kg_orphan_bridge_u10mo_u3si_puo2", "8f3a1c2d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
