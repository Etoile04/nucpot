"""merge multiple alembic heads (033 classification + 035 multimodal)

Revision ID: 036_merge_heads_33_35
Revises: 033_add_classification_enforcement, 035_add_extraction_job_multimodal_flags
Create Date: 2026-07-30 16:55:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "036_merge_heads_33_35"
down_revision: Union[str, Sequence[str], None] = (
    "033_add_classification_enforcement",
    "035_add_extraction_job_multimodal_flags",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No DDL — purely a merge revision so the chain has a single head.
    # NFM-2137 (4eab448) added 035_add_extraction_job_multimodal_flags chained
    # off 033_add_conditions_hash_and_method_to_measurements, leaving
    # 033_add_classification_enforcement as a parallel head. This merge ties
    # the two branches together so `alembic upgrade head` succeeds.
    pass


def downgrade() -> None:
    # No DDL to revert; the underlying tables are unchanged by this merge.
    pass
