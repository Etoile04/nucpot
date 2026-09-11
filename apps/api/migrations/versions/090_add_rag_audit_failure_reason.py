"""090 — failure_reason column on rag_index_audit_log (NFM-4742 F-3 §3.2).

NFM-4738 evidence (pin main, 09-11 10:20Z) shows the LightRAG ``/documents``
``failed`` bucket mixed three categorically distinct outcomes:

* ``duplicate``   — LightRAG dedupe rejected the doc (``Identical content
                   already exists`` or ``File name already exists``).
                   Health-neutral: dedupe is doing its job.
* ``error``       — chunking / LLM extraction failed with a non-empty
                   error_message.  Actionable: re-ingest or fix content.
* ``empty``       — chunking failed with an empty error_message
                   (C[1/13]: doc-…-chunk-003: …).  Actionable: replay the
                   doc with a different split to capture a real error.

Until now every ``failed`` row landed in ``rag_index_audit_log`` with
``action='error'`` and the raw error_message — so health dashboards could
not distinguish dedupe (the system working as intended) from a real
regression (10 stale ``nfm-4505-fresh-*`` docs since 09-08).

This migration adds a nullable ``failure_reason`` column carrying one of
``duplicate | error | empty | timeout | unknown`` so the 03:30 UTC
``rag_audit_document_buckets_task`` (NFM-4742 F-3 §4) can write
segregated audit rows that do not pollute the daily health metric.

The column is intentionally NOT ``NOT NULL``: pre-NFM-4742 audit rows
recorded ``action='reingest' | 'noop' | 'stale' | 'error'`` without a
bucket classification, and backfilling historical rows is out of scope.

Revision ID: 090_add_rag_audit_failure_reason
Revises: 089_add_ix_verification_tasks_status
Create Date: 2026-09-12

Coordination
------------
* Blocks: NFM-4736 F1 acceptance — the rebuild's "failed-bucket-is-clean"
  gate reads ``failure_reason='error' OR 'empty'`` rows only; dedupe
  rows must not count toward that gate.
* Companion PR: NFM-4742 (same branch, separate commits).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "090_add_rag_audit_failure_reason"
down_revision: str | Sequence[str] | None = "089_add_ix_verification_tasks_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "rag_index_audit_log"
COLUMN = "failure_reason"
INDEX_NAME = "ix_rag_index_audit_failure_reason"

# Mirrors ``nfm_db.services.rag_audit.classify_failure_reason``; keep
# these strings byte-identical or the audit task will emit values that
# the SQL ``WHERE failure_reason='duplicate'`` predicate in the F1
# acceptance gate never matches.
_FAILURE_REASON_VALUES: tuple[str, ...] = (
    "duplicate",
    "error",
    "empty",
    "timeout",
    "unknown",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns(TABLE)}
    if COLUMN not in existing_cols:
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.String(length=32), nullable=True),
        )
    existing_idx = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    if INDEX_NAME not in existing_idx:
        # B-tree on the (low-cardinality) reason column speeds up the F1
        # acceptance gate's ``WHERE failure_reason IN ('error','empty')``
        # query when the audit log grows to millions of rows.
        op.create_index(INDEX_NAME, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_idx = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    if INDEX_NAME in existing_idx:
        op.drop_index(INDEX_NAME, table_name=TABLE)
    existing_cols = {c["name"] for c in inspector.get_columns(TABLE)}
    if COLUMN in existing_cols:
        op.drop_column(TABLE, COLUMN)


__all__ = ["_FAILURE_REASON_VALUES"]
