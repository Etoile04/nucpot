"""unite 070 extraction gap with 073 preview role (NFM-4076)

Revision ID: 40782ad79e2e
Revises: 070_extraction_gap_orm_column_align, 073_create_nfm_preview_role
Create Date: 2026-09-02 15:12:25.474646

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40782ad79e2e'
down_revision: Union[str, Sequence[str], None] = ('070_extraction_gap_orm_column_align', '073_create_nfm_preview_role')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
