"""090 — lightrag_doc_failure table (NFM-4743).

Mirrors the LightRAG ``/documents`` ``failed`` bucket into a queryable
PG table with an explicit ``failure_kind`` column
(``duplicate`` | ``error`` | ``processing_timeout``).

NFM-4738 F-3 audit (rag-comprehensive-test-report-2026-09-11.md) showed
the LightRAG ``failed`` bucket is 30 rows split as 20 dedupe-rejected
("Identical content already exists" / "File name already exists") and
10 real chunking/extraction failures.  Conflating them in a single
``failed`` count polluted the health metric and masked the real errors.

Composite unique key on ``doc_id`` is the idempotency guard for
``record_doc_failures`` so the daily beat can replay the same payload
without inflating the table.

Chains after 089 (ix_verification_tasks_status).

Revision ID: 090_add_lightrag_doc_failure
Revises: 089_add_ix_verification_tasks_status
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "090_add_lightrag_doc_failure"
down_revision: str | Sequence[str] | None = "089_add_ix_verification_tasks_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lightrag_doc_failure (
            id UUID PRIMARY KEY,
            doc_id VARCHAR(256) NOT NULL,
            file_source VARCHAR(512),
            failure_kind VARCHAR(32) NOT NULL,
            error_message TEXT,
            recorded_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_lightrag_doc_failure_doc_id UNIQUE (doc_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lightrag_doc_failure_kind "
        "ON lightrag_doc_failure (failure_kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lightrag_doc_failure_last_seen_at "
        "ON lightrag_doc_failure (last_seen_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lightrag_doc_failure")
