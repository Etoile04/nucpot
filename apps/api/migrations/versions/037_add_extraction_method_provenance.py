"""Add extraction_method provenance columns (NFM-2247)

Revision ID: 037_add_extraction_method_provenance
Revises: 036_merge_chain_A_and_B
Create Date: 2026-07-31

NFM-2247 — unblock NFM-2237 (DetailPanel provenance labels). The literature
detail payload is assembled from three tables and none of them recorded *how*
an item was produced, so every row rendered ``来源未知``:

  * ``extraction_results`` — the legacy branch
  * ``kg_nodes``           — the OntoFuel pipeline branch
  * ``kg_edges``           — merged into the same array as nodes

The existing "Source provenance" fields (``source_paragraph`` / ``source_page``
/ ``source_doi``) and the ``kg_provenance`` table are *document* traceability:
they answer "where in the paper did this come from". This column answers a
different question — "which producer wrote this row" — and is written by each
producing path (see ``nfm_db.services.provenance``).

A fourth table, ``extraction_figures``, already has an ``extraction_method``
column (added by migration 026), so it needs no DDL here; NFM-2247 only added
the missing declaration on the ORM model.

Columns added (mirroring the SQLAlchemy models):

  * extraction_results.extraction_method  VARCHAR(100) NULL
  * kg_nodes.extraction_method            VARCHAR(100) NULL
  * kg_edges.extraction_method            VARCHAR(100) NULL

Backfill decision — NULL, i.e. explicit unknown
-----------------------------------------------
Existing rows are left NULL rather than being defaulted to ``'llm'``. The
provenance of historical rows genuinely is not known: ``extraction_results``
predates the OntoFuel pipeline and has no surviving producer record, and
``kg_nodes`` / ``kg_edges`` rows may have come from the seed loader
(``seed_ontofuel``) rather than from extraction. Guessing ``'llm'`` would
label seeded and hand-loaded data as model output, which is exactly the
mislabelling the NFM-2247 AC forbids ("Prefer an explicit unknown over a
guess"). The API maps NULL to an empty list and the frontend badge renders
that as ``来源未知``, which is both truthful and already implemented.

There is deliberately no ``server_default``: a default would silently stamp a
provenance onto any future INSERT that forgot to set one, re-introducing the
same mislabelling risk for new writes.

Implementation note:
  Same hand-rolled DDL pattern as migrations 034/035 — raw ``ALTER TABLE ...
  ADD COLUMN IF NOT EXISTS`` on PostgreSQL (idempotent, and metadata-only in
  PG 11+ for a nullable column with no default), and ``op.add_column`` on
  SQLite tolerating the ``OperationalError`` raised when the column already
  exists.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import OperationalError

# revision identifiers, used by Alembic.
revision: str = "037_add_extraction_method_provenance"
down_revision: str | Sequence[str] | None = "036_merge_chain_A_and_B"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES: tuple[str, ...] = ("extraction_results", "kg_nodes", "kg_edges")

_COLUMN = "extraction_method"
_COMMENT = "llm | manual | mineru (comma-joined when several apply); NULL = unknown"

# PG DDL — derived directly from the SQLAlchemy models. Nullable with no
# DEFAULT, so this is a metadata-only change even on large tables.
_PG_ADD_COLUMNS: tuple[str, ...] = tuple(
    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {_COLUMN} VARCHAR(100)"
    for table in _TABLES
)


def upgrade() -> None:
    """Add the nullable extraction_method column to the three tables."""
    if op.get_bind().dialect.name == "postgresql":
        for statement in _PG_ADD_COLUMNS:
            op.execute(statement)
        for table in _TABLES:
            op.execute(f"COMMENT ON COLUMN {table}.{_COLUMN} IS '{_COMMENT}'")
        return

    # SQLite (test path) has no ADD COLUMN IF NOT EXISTS.
    for table in _TABLES:
        try:
            op.add_column(
                table,
                sa.Column(_COLUMN, sa.String(100), nullable=True, comment=_COMMENT),
            )
        except OperationalError:
            # Column already present — the migration is safe to re-run.
            pass


def downgrade() -> None:
    """Drop the extraction_method column from the three tables."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {_COLUMN}")
        return

    for table in _TABLES:
        try:
            op.drop_column(table, _COLUMN)
        except OperationalError:
            pass
