"""merge the 036_ref gap-fill chain into 036_merge_chain_A_and_B

Revision ID: 037_merge_ref_gap_fill_chain
Revises: 036_merge_chain_A_and_B, 036_ref_gap_fill_staging_v4_columns_simple
Create Date: 2026-07-31 06:27:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "037_merge_ref_gap_fill_chain"
down_revision: Union[str, Sequence[str], None] = (
    "036_merge_chain_A_and_B",
    "036_ref_gap_fill_staging_v4_columns_simple",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No DDL — purely a chain consolidation so `alembic upgrade head` succeeds.
    # NFM-2147 D4 added a second chain off the 034 branchpoint
    # (035_ref_gap_fill_staging_v4_columns -> 036_ref_..._simple) while
    # 036_merge_chain_A_and_B already consolidated the multimodal chain off
    # that same parent. That left two heads, which fails three gates:
    #   1. tools/pre-deploy-assert-smoke/assert.sh STRICT_HEADS (exit 65),
    #      a hard `needs:` for deploy-prod;
    #   2. scripts/prod_migrate.sh `alembic upgrade head` ("Multiple head
    #      revisions are present"), which now runs BEFORE `compose up -d`;
    #   3. the "Enforce single alembic head" gate in test-api.yml.
    # This merge ties the two heads so the branch has exactly one head.
    pass


def downgrade() -> None:
    # No DDL to revert.
    pass
