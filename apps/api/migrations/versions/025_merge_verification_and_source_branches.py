"""merge_verification_and_source_branches

Merge the two alembic heads:
- 024 (create_verification_tasks_table) descends from 023
- 054b39a26310 (add_source_to_dft_calculations) descends from f8e2db803b55

This unites the forked migration graph (NFM-167).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | Sequence[str] | None = ("024", "054b39a26310")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
