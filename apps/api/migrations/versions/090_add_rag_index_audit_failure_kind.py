"""090 — rag_index_audit_log.failure_kind (NFM-4742-B / NFM-4744)

Adds a first-class ``failure_kind`` column on ``rag_index_audit_log``
so the processing-timeout sweep can record *why* a row transitioned to
``failed`` without overloading ``error_message``.  The two failure-taxonomy
siblings (NFM-4742-A ``failure_reason`` and NFM-4742-B ``failure_kind``)
are deliberately scoped to their own sub-tasks; the integration task
(NFM-4742-INTEG) reconciles them at merge time.

The column is nullable so pre-NFM-4742 audit rows keep their shape, and
indexed because the F-3 acceptance gate filters on
``WHERE failure_kind = 'processing_timeout'`` to compute the
"how-many-rows-did-the-reaper-save" health metric.

Revision ID: 090_add_rag_index_audit_failure_kind
Revises: 089_add_ix_verification_tasks_status
"""

from collections.abc import Sequence

from alembic import op

revision: str = "090_add_rag_index_audit_failure_kind"
down_revision: str | Sequence[str] | None = "089_add_ix_verification_tasks_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "rag_index_audit_log"
COLUMN = "failure_kind"
INDEX_NAME = "ix_rag_index_audit_failure_kind"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {COLUMN} VARCHAR(32)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE} ({COLUMN})"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {COLUMN}")
