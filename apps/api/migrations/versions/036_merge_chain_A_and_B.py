"""merge chain A (032_create_data_submission_tables) with chain B (035_multimodal)

Revision ID: 036_merge_chain_A_and_B
Revises: 032_create_data_submission_tables, 035_add_extraction_job_multimodal_flags
Create Date: 2026-07-30 17:25:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "036_merge_chain_A_and_B"
down_revision: Union[str, Sequence[str], None] = (
    "032_create_data_submission_tables",
    "035_add_extraction_job_multimodal_flags",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No DDL — purely a chain consolidation so `alembic upgrade head` succeeds.
    # The D1 workflow runs `alembic upgrade head` on every container start.
    # Chain A (032_create_data_submission_tables) and Chain B (035_multimodal)
    # were left as parallel heads after NFM-2137; this merge ties them.
    # The 033_add_classification_enforcement (chain A's original head) was
    # removed in 9d48217 because its DDL conflicts with hub_nodes that
    # chain B already created.
    pass


def downgrade() -> None:
    # No DDL to revert.
    pass
