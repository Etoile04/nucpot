"""Add 'cancelled' to md_verification_jobs status check constraint.

NFM-388: The JobStatus.CANCELLED enum value was added to the model
but the original migration (003) only included five statuses in the
check_md_job_status constraint.  This migration extends the constraint
so the cancel_md_verification_job endpoint can set status to
'cancelled' without violating the DB constraint.

Revision ID: 006
Revises: 005
Create Date: 2026-06-23

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: str | Sequence[str] | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "status IN ('pending', 'submitted', 'running', 'completed', 'failed')"
_NEW_CONSTRAINT = "status IN ('pending', 'submitted', 'running', 'completed', 'failed', 'cancelled')"
_CONSTRAINT_NAME = "check_md_job_status"
_TABLE = "md_verification_jobs"


def upgrade() -> None:
    """Extend check_md_job_status to include 'cancelled'."""
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        _NEW_CONSTRAINT,
    )


def downgrade() -> None:
    """Revert check_md_job_status to exclude 'cancelled'.

    Note: this will fail if any rows currently have status='cancelled'.
    """
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        _OLD_CONSTRAINT,
    )
