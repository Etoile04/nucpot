"""Add extraction_jobs persistence columns (NFM-2115 / NFM-2013 AC-2+AC-5)

Revision ID: 034_add_extraction_job_persistence_columns
Revises: 033_add_conditions_hash_and_method_to_measurements
Create Date: 2026-07-30

NFM-2115 — Close the production schema gap left by the NFM-2013 hotfix
(commit 62491b0). The hotfix extended the SQLAlchemy ``ExtractionJob`` model
in ``apps/api/src/nfm_db/models/extraction_job.py`` with the provenance,
status, count and timestamp columns listed below, and ``ingest_extraction_batch``
now writes a row with those fields on every POST. The matching
``ALTER TABLE extraction_jobs ADD COLUMN ...`` step never landed in any
migration that reached production, so the running prod image's INSERT
crashes with::

    asyncpg.UndefinedColumnError:
        column "source_reference" of relation "extraction_jobs" does not exist

Prod fact pattern (verified via ``docker exec nucpot-prod-db psql \\d+ extraction_jobs``):
only four columns exist on the prod table::

    id, status, created_at, updated_at

and the alembic head is ``033_add_conditions_hash_and_method_to_measurements``.

This migration chains off that prod head and adds the 13 columns the
running API now requires. Every ADD COLUMN uses ``IF NOT EXISTS`` semantics
so the migration is idempotent and safe to re-run against a partially-migrated
DB (e.g. if a future hotfix adds a subset of these columns first).

Columns added (mirroring ``apps/api/src/nfm_db/models/extraction_job.py``):

Provenance (NFM-2013 AC-2)
  * source_reference VARCHAR(500)                     NULL
  * source_type      VARCHAR(20)                      NULL
  * corpus_id        VARCHAR(100)                     NULL

Status (NFM-2013 AC-5)
  * error_message    TEXT                             NULL

Counts (OntoFuel handoff contract) — INTEGER NOT NULL DEFAULT 0
  * total_received
  * created_measurements
  * reused_entities
  * skipped_duplicate_measurements
  * skipped_unknown_properties
  * skipped_duplicates
  * validation_errors

Timestamps — TIMESTAMP WITH TIME ZONE NULL
  * started_at
  * completed_at

Implementation note (NFM-2115 follow-up):
  The previous version of this migration called ``sa.Column.compile(dialect=...)``
  in the PostgreSQL branch, which returned ONLY the column name (no type, no
  NOT NULL, no server_default) and therefore emitted invalid ``ALTER TABLE ...
  ADD COLUMN IF NOT EXISTS <name>`` statements. The SQLite test path went
  through ``op.add_column(...)`` which uses ``CreateColumn`` internally, so
  the SQLite suite passed but the PG branch was untested. This rewrite drops
  the helper entirely and uses raw ``ALTER TABLE`` strings — the 13 columns
  are fixed, hand-rolled DDL is the simplest, audit-friendly form, and a
  real-Postgres verification step is now mandatory before re-handoff.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034_add_extraction_job_persistence_columns"
down_revision: str | Sequence[str] | None = "033_add_conditions_hash_and_method_to_measurements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Column specs. Each entry is the literal ``ALTER TABLE extraction_jobs
# ADD COLUMN IF NOT EXISTS ...`` fragment emitted on PostgreSQL. The SQLite
# path uses the equivalent ``op.add_column(...)`` with hand-built Column
# objects (so the test-DDL parity still holds but the SQLite and PG paths
# remain fully independent).
# ---------------------------------------------------------------------------

# PG DDL — derived directly from apps/api/src/nfm_db/models/extraction_job.py.
_PG_ADD_COLUMNS: tuple[str, ...] = (
    # --- Provenance (NFM-2013 AC-2) ---
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS source_reference VARCHAR(500)",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS source_type VARCHAR(20)",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS corpus_id VARCHAR(100)",
    # --- Status (NFM-2013 AC-5) ---
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS error_message TEXT",
    # --- Counts (OntoFuel handoff contract) ---
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS total_received INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS created_measurements INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS reused_entities INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS skipped_duplicate_measurements INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS skipped_unknown_properties INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS skipped_duplicates INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS validation_errors INTEGER NOT NULL DEFAULT 0",
    # --- Timestamps ---
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE",
)

# Down migration: drop the same 13 columns. Order does not matter (no FKs
# link these columns), but we list them in reverse-add order for hygiene.
_PG_DROP_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS completed_at",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS started_at",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS validation_errors",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS skipped_duplicates",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS skipped_unknown_properties",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS skipped_duplicate_measurements",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS reused_entities",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS created_measurements",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS total_received",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS error_message",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS corpus_id",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS source_type",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS source_reference",
)

# SQLite specs: same column shape, expressed as ``op.add_column`` calls.
# ``op.add_column`` accepts the full Column object, so the count columns
# carry an explicit ``server_default`` so legacy rows inserted before
# migration 034 receive a valid default during column addition — keeping
# the NOT NULL constraint satisfiable for backfilled data.
import sqlalchemy as sa  # noqa: E402  (post-typing-import to keep PG block first)

_SQLITE_ADD_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("source_reference", sa.String(500), nullable=True),
    sa.Column("source_type", sa.String(20), nullable=True),
    sa.Column("corpus_id", sa.String(100), nullable=True),
    sa.Column("error_message", sa.Text(), nullable=True),
    sa.Column("total_received", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_measurements", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("reused_entities", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_duplicate_measurements", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_unknown_properties", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_duplicates", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("validation_errors", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    """Add 13 columns to extraction_jobs so the running API can INSERT.

    Uses raw PG ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` — the previous
    implementation invoked ``sa.Column.compile(dialect=...)`` which produces
    only the column name (no type, no constraints) and therefore could not
    have produced valid DDL on PG. Raw SQL keeps the migration auditable
    and trivially re-runnable.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        for stmt in _PG_ADD_COLUMNS:
            op.execute(stmt)
        return
    # SQLite (test path): ``ADD COLUMN IF NOT EXISTS`` does not exist; rely
    # on ``op.add_column`` and tolerate ``OperationalError`` so the test
    # suite can re-run the migration. ``server_default="0"`` on each count
    # column satisfies the NOT NULL constraint for legacy backfill rows.
    import sqlalchemy.exc  # local import — only the SQLite branch needs it

    for col in _SQLITE_ADD_COLUMNS:
        try:
            op.add_column("extraction_jobs", col)
        except sqlalchemy.exc.OperationalError:
            # Column already exists from a prior partial run. Migration is
            # idempotent at the test-DDL level too.
            pass


def downgrade() -> None:
    """Drop the 13 columns added by upgrade(). Idempotent on PG and SQLite."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        for stmt in _PG_DROP_COLUMNS:
            op.execute(stmt)
        return
    import sqlalchemy.exc  # local import — only the SQLite branch needs it

    for col in reversed(_SQLITE_ADD_COLUMNS):
        try:
            op.drop_column("extraction_jobs", col.name)
        except sqlalchemy.exc.OperationalError:
            pass
