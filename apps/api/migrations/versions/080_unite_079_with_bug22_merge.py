"""unite 079 with 40782ad79e2e merge branch

Revision ID: 8f3a1c2d4e5f
Revises: 079_restore_070_measurement_casualties, 40782ad79e2e
Create Date: 2026-09-03

NFM-167: unite forked migration heads introduced when main added
079_restore_070_measurement_casualties while the bug22 branch carried
its own merge revision 40782ad79e2e.
"""

from typing import Sequence

from alembic import op

revision: str = "8f3a1c2d4e5f"
down_revision: str | Sequence[str] | None = ("079_restore_070_measurement_casualties", "40782ad79e2e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
