"""086 — rag_access_log table (NFM-4539 RAG-B).

One row per ``POST /api/v1/lightrag/query`` call.  Surfaces the AC-7
telemetry: ``mode``, ``was_fallback``, ``was_cached``, ``query_kind``,
``result_count``, ``time_total``.  Indexed on ``ts`` for the AC-5
P95-over-7d rollups, on ``(mode, ts)`` for per-mode dashboards, and on
``was_fallback`` so AC-4 fallback counters stay cheap.

Chains after ``085_g1b_conditions_dataset_versions_dedupe`` (NFM-4548 G1-B
schema) on the post-2026-09-10 main branch — the original branch-local
revision 085 collided with NFM-4548 and was renumbered to 086 during the
rebase onto ``abc345a39``.

Revision ID: 086_create_rag_access_log
Revises: 085_g1b_conditions_dataset_versions_dedupe
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "086_create_rag_access_log"
down_revision: str | Sequence[str] | None = "085_g1b_conditions_dataset_versions_dedupe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_access_log (
            id UUID PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            mode VARCHAR(32) NOT NULL,
            was_fallback BOOLEAN NOT NULL DEFAULT FALSE,
            was_cached BOOLEAN NOT NULL DEFAULT FALSE,
            query_kind VARCHAR(16),
            result_count INTEGER NOT NULL DEFAULT 0,
            time_total DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            error_message VARCHAR(512)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_access_log_ts "
        "ON rag_access_log (ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_access_log_mode_ts "
        "ON rag_access_log (mode, ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_access_log_was_fallback "
        "ON rag_access_log (was_fallback)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_access_log")