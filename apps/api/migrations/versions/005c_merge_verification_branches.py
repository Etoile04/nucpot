"""Merge verification branches (005a + 005b).

Unites the two lineages that forked at the duplicate revision 005:
- 005a: verification_status column on potentials (from 003→004)
- 005b: verification_results_md table + md_jobs extensions (from 003b→004)

Revision ID: 005c
Revises: 005a, 005b
Create Date: 2026-07-13

"""

from collections.abc import Sequence

revision: str = "005c"
down_revision: Sequence[str] | None = ("005a", "005b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
