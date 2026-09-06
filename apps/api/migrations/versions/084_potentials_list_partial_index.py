"""Partial index for the potentials list default sort (NFM-4311, BUG-30).

GET /api/v1/potentials (now the backend for the web list view) filters
``status = 'published'`` and orders by ``updated_at DESC`` for every
unfiltered page view. Existing indexes only cover ``status`` and ``type``
in isolation, so PG sorts the full published set on every request —
irrelevant at the current 65-row corpus (sub-ms), but it grows with the
corpus while the design target (<500ms p50) must not (NFM-4311 AC #2).
A partial b-tree on the published slice keeps the default sort index-backed
at any scale.

EXPLAIN (ANALYZE) measured on the prod DB (65 published rows, 2026-09-05,
index created inside a rolled-back transaction):
  before:  Limit -> Sort (top-N heapsort) -> Seq Scan   0.093ms
  after:   Limit -> Index Scan Backward (this index)    0.047ms
The planner picks the index naturally even at 65 rows; the point of the
partial index is that the default sort stays index-backed (O(log n + k),
no full-slice sort) as the corpus grows.

Revision ID: 084_potentials_list_partial_index
Revises: 083_normalize_potential_file_urls
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "084_potentials_list_partial_index"
down_revision: str | Sequence[str] | None = "083_normalize_potential_file_urls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_potentials_published_updated_at"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "potentials",
        ["updated_at"],
        unique=False,
        postgresql_where=sa.text("status = 'published'"),
        sqlite_where=sa.text("status = 'published'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="potentials")
