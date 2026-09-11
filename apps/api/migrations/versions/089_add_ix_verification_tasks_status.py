"""add ix_verification_tasks_status (latent schema-drift fix)

VerificationTask.status has carried ``index=True`` since the model landed
(NFM-1775 era), but no migration ever created the index — the
schema-drift guard flags ``missing_index ix_verification_tasks_status``
on every PR that runs it, currently blocking NFM-4620 (PR #1296) and,
transitively, the NFM-4625 beat landing.

Revision ID: 089_add_ix_verification_tasks_status
Revises: 088_add_validity_check_and_valid_range
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "089_add_ix_verification_tasks_status"
down_revision: str | Sequence[str] | None = "088_add_validity_check_and_valid_range"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_verification_tasks_status"
TABLE = "verification_tasks"
COLUMN = "status"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    if INDEX_NAME not in existing:
        op.create_index(INDEX_NAME, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, TABLE_NAME=TABLE)
