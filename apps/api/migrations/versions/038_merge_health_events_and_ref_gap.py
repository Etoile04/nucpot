"""Merge health_events chain and ref_gap_fill chain into single head.

Revision ID: 038_merge_health_events_and_ref_gap
Revises: 037_create_health_events_table, 037_merge_ref_gap_fill_chain
Create Date: 2026-07-31

Auto-merge: two independent chains both branch off 036_merge_chain_A_and_B:
  - 037_create_health_events_table (NFM-2220 / NFM-2241)
  - 037_merge_ref_gap_fill_chain (NFM-2196 / NFM-2147 D4)

Both landed in PR #556 and #499 respectively on the same day, creating
a forked alembic graph. This merge revision reunifies them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "038_merge_health_events_and_ref_gap"
down_revision: Sequence[str] | None = (
    "037_create_health_events_table",
    "037_merge_ref_gap_fill_chain",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
