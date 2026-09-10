"""087 — rag_index_audit_log table (NFM-4539 RAG-D).

One row per drift finding emitted by the ``rag_audit_index_coverage``
Celery task (NFM-4257 03:30 UTC window).  Composite unique constraint
``(routine, literature_id, run_date)`` is the §8.2 idempotency guard.

Chains after ``086_create_rag_access_log`` (NFM-4539 RAG-B AC-7) —
originally branch-local revision 086, renumbered to 087 during the
rebase onto ``abc345a39`` (main).

Revision ID: 087_create_rag_index_audit_log
Revises: 086_create_rag_access_log
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "087_create_rag_index_audit_log"
down_revision: str | Sequence[str] | None = "086_create_rag_access_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_index_audit_log (
            id UUID PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            routine VARCHAR(64) NOT NULL DEFAULT 'rag_audit_index_coverage',
            run_date DATE NOT NULL,
            literature_id UUID,
            action VARCHAR(16) NOT NULL,
            error_message TEXT,
            CONSTRAINT uq_rag_index_audit_natural_key
                UNIQUE (routine, literature_id, run_date)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_index_audit_run_date "
        "ON rag_index_audit_log (run_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_index_audit_action "
        "ON rag_index_audit_log (action)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_index_audit_log")