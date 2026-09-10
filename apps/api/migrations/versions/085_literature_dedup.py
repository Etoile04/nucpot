"""Literature ingestion dedup (NFM-4549, G1-C).

Implements the schema half of ADR-017 §2.5 + §2.6:

* ``datasets.literature_doi`` — nullable, **partial UNIQUE** so two
  datasets can both have NULL DOIs (legacy rows) but a real DOI
  belongs to at most one dataset. The literature_service normalizes
  the DOI before lookup, so partial-uniqueness on the canonicalized
  form is the right shape.

* ``datasets.literature_content_hash`` — nullable, **partial UNIQUE**
  for the same reason: a PDF SHA-256 identifies a literature, but
  legacy datasets without a recorded hash must coexist.

* ``property_measurements.dedupe_key`` — **nullable**, full UNIQUE
  constraint. This is the AC-9 hook: Owen 2023's 92-row case study
  (same value repeated) collapses to one row once the mapper writes
  ``compute_dedupe_key(...)``. The constraint is the load-bearing
  invariant — even if two writers race, only one INSERT survives.

Notes
=====

* ``dedupe_key`` is intentionally **nullable** (not NOT NULL):
  legacy rows predate the dedup contract, and a backfill in this
  migration would have to derive ``value_hash`` from heterogeneous
  legacy data (some rows use ``value_text`` only, some use
  ``value_scalar``, etc.) — a single shape does not fit. PG and
  SQLite both permit multiple NULLs in a UNIQUE index by SQL spec,
  so legacy rows coexist without a backfill. The mapper
  (``extraction_to_db_mapper.map_and_persist``) is the only
  writer-side caller of ``compute_dedupe_key``; it MUST populate
  the column on every new INSERT (verified end-to-end by
  ``tests/test_literature_dedup.py::TestMapperWritesDedupeKey``).
  AC-9 is therefore enforced at the row level (every mapper write)
  not at the column level (NOT NULL + backfill).

* The mapper in ``extraction_to_db_mapper`` writes the
  ``dedupe_key`` column on INSERT. This migration only adds the
  schema — the writer wire-up is in this same issue
  (``extraction_to_db_mapper`` — NFM-4549, NOT NFM-4547: G1-A
  owns the prompt / adapter / lock file, not the DB mapper). AC-9
  is verified at the DB level by
  ``tests/test_literature_dedup.py::TestDedupeKeyUniqueConstraint``.

* ``literature_doi`` is populated by ``extraction_to_db_mapper`` when
  a fresh ``Dataset`` row is created (via ``normalize_doi(source.doi)``
  from the parent ``DataSource``). Legacy datasets may carry NULL until
  a curator backfills them. Partial UNIQUE keeps NULL rows legal.

* Partial UNIQUE indexes (``WHERE col IS NOT NULL``) are PG and
  SQLite-3.8+ compatible. SQLite's UNIQUE constraint doesn't accept
  a WHERE clause, so we materialize the constraint with two partial
  indexes that SQLAlchemy compiles to ``CREATE UNIQUE INDEX ... WHERE ...``.

Revision ID: 085_literature_dedup
Revises: 084_potentials_list_partial_index
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "085_literature_dedup"
down_revision: str | Sequence[str] | None = "084_potentials_list_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DOI_INDEX = "uq_datasets_literature_doi"
_CONTENT_HASH_INDEX = "uq_datasets_literature_content_hash"
_DEDUPE_INDEX = "uq_property_measurements_dedupe_key"


def _is_sqlite() -> bool:
    """True iff the bound alembic connection is SQLite (test env)."""
    return op.get_bind().dialect.name == "sqlite"


def _partial_unique(
    table: str,
    column: str,
    index_name: str,
) -> None:
    """Create a partial UNIQUE index, conditional on SQLite vs PG.

    Postgres accepts ``CREATE UNIQUE INDEX ... WHERE col IS NOT NULL``
    natively; SQLite needs ``sqlite_where`` so SQLAlchemy emits the
    matching ``WHERE`` clause.
    """
    where = sa.text(f"{column} IS NOT NULL")
    op.create_index(
        index_name,
        table,
        [column],
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )


def upgrade() -> None:
    # --- datasets.literature_doi ----------------------------------------
    op.add_column(
        "datasets",
        sa.Column(
            "literature_doi",
            sa.String(length=512),
            nullable=True,
            comment=(
                "Normalized DOI (URL/whitespace stripped, lowercased). "
                "Partial-unique: NULL allowed for legacy rows."
            ),
        ),
    )
    _partial_unique("datasets", "literature_doi", _DOI_INDEX)

    # --- datasets.literature_content_hash -------------------------------
    op.add_column(
        "datasets",
        sa.Column(
            "literature_content_hash",
            sa.String(length=128),
            nullable=True,
            comment=(
                "SHA-256 of the canonical PDF bytes (or normalized "
                "content_md). Fallback dedup key when no DOI."
            ),
        ),
    )
    _partial_unique("datasets", "literature_content_hash", _CONTENT_HASH_INDEX)

    # --- property_measurements.dedupe_key -------------------------------
    # Nullable: the mapper (``extraction_to_db_mapper`` — NFM-4549 /
    # G1-C) writes the column on INSERT. The UNIQUE index still enforces
    # AC-9 once rows are populated. PG + SQLite both permit multiple
    # NULLs in a UNIQUE column by default, so legacy rows coexist.
    op.add_column(
        "property_measurements",
        sa.Column(
            "dedupe_key",
            sa.String(length=128),
            nullable=True,
            comment=(
                "sha256(dataset|property|source|value_hash) per ADR-017 §2.6. "
                "Mapper MUST populate on new rows; legacy/admin rows may be NULL."
            ),
        ),
    )
    op.create_index(
        _DEDUPE_INDEX,
        "property_measurements",
        ["dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(_DEDUPE_INDEX, table_name="property_measurements")
    op.drop_column("property_measurements", "dedupe_key")

    op.drop_index(_CONTENT_HASH_INDEX, table_name="datasets")
    op.drop_column("datasets", "literature_content_hash")

    op.drop_index(_DOI_INDEX, table_name="datasets")
    op.drop_column("datasets", "literature_doi")
