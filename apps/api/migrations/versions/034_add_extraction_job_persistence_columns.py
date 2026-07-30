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
  * source_reference VARCHAR(500)  NULL
  * source_type      VARCHAR(20)   NULL
  * corpus_id        VARCHAR(100)  NULL

Status (NFM-2013 AC-5)
  * error_message    TEXT          NULL

Counts (OntoFuel handoff contract)
  * total_received               INT NOT NULL DEFAULT 0
  * created_measurements         INT NOT NULL DEFAULT 0
  * reused_entities              INT NOT NULL DEFAULT 0
  * skipped_duplicate_measurements INT NOT NULL DEFAULT 0
  * skipped_unknown_properties   INT NOT NULL DEFAULT 0
  * skipped_duplicates           INT NOT NULL DEFAULT 0
  * validation_errors            INT NOT NULL DEFAULT 0

Timestamps
  * started_at    TIMESTAMP WITH TIME ZONE NULL
  * completed_at  TIMESTAMP WITH TIME ZONE NULL

The migration is reversible (down drops the columns). ONTO the SQLite test
DB, ``ADD COLUMN IF NOT EXISTS`` is unavailable, so the helper
``_safe_add_column`` falls back to ``try/except`` mirroring migration 033.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034_add_extraction_job_persistence_columns"
down_revision: str | Sequence[str] | None = "033_add_conditions_hash_and_method_to_measurements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _safe_add_column(
    table: str,
    column: sa.Column,
    *,
    if_not_exists: bool = True,
) -> None:
    """Add a column to ``table``, idempotently.

    On PostgreSQL we use ``ADD COLUMN IF NOT EXISTS`` (PG 9.6+) so re-running
    the migration on a partially-migrated DB is safe and produces no error.
    On SQLite (used by the test suite) ``ADD COLUMN IF NOT EXISTS`` does
    NOT exist, so we fall back to a ``try/except`` around the plain
    ``ADD COLUMN`` and swallow the duplicate-column error.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql" and if_not_exists:
        # Compile the column to its DDL fragment (column name + type + constraints)
        # then wrap it in ADD COLUMN IF NOT EXISTS so re-runs are no-ops.
        col_ddl = str(column.compile(dialect=bind.dialect)).strip()
        op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_ddl}')
        return
    # SQLite / unknown: best-effort; swallow duplicate-column errors.
    try:
        op.add_column(table, column)
    except Exception:
        # Column already exists. Re-running the migration must be a no-op.
        pass


def _safe_drop_column(table: str, column_name: str) -> None:
    """Drop a column from ``table``, tolerating 'does not exist' errors.

    Mirrors the idempotency of ``_safe_add_column`` so downgrade is safe
    even if a partial downgrade already ran.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS {column_name}')
        return
    try:
        op.drop_column(table, column_name)
    except Exception:
        pass


def upgrade() -> None:
    """Add 13 columns to extraction_jobs so the running API can INSERT."""
    # --- Provenance (NFM-2013 AC-2) ---
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "source_reference",
            sa.String(500),
            nullable=True,
            comment="DOI / URL / file path the batch was extracted from.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "source_type",
            sa.String(20),
            nullable=True,
            comment="doi | url | file | internal_id | datasource.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "corpus_id",
            sa.String(100),
            nullable=True,
            comment="External corpus slug the batch was tagged with.",
        ),
    )

    # --- Status (NFM-2013 AC-5) ---
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Last failure reason when status='failed'.",
        ),
    )

    # --- Counts (NFM-2013 AC-5 / OntoFuel handoff contract) ---
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "total_received",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Total property records received in the request payload.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "created_measurements",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Property measurements persisted to DB.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "reused_entities",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Existing DB entities reused (DataSource/Material already in DB).",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "skipped_duplicate_measurements",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Duplicate measurements skipped (NFM-2032 5-tuple dedup).",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "skipped_unknown_properties",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Records skipped because the property type was unknown.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "skipped_duplicates",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Backward-compat alias: "
                "reused_entities + skipped_duplicate_measurements + "
                "skipped_unknown_properties."
            ),
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "validation_errors",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Records rejected by the validation gate.",
        ),
    )

    # --- Timestamps ---
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time the handler began processing the batch.",
        ),
    )
    _safe_add_column(
        "extraction_jobs",
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time the handler finished (success or failure).",
        ),
    )


def downgrade() -> None:
    """Drop the 13 columns added by upgrade(). Idempotent."""
    _safe_drop_column("extraction_jobs", "completed_at")
    _safe_drop_column("extraction_jobs", "started_at")
    _safe_drop_column("extraction_jobs", "validation_errors")
    _safe_drop_column("extraction_jobs", "skipped_duplicates")
    _safe_drop_column("extraction_jobs", "skipped_unknown_properties")
    _safe_drop_column("extraction_jobs", "skipped_duplicate_measurements")
    _safe_drop_column("extraction_jobs", "reused_entities")
    _safe_drop_column("extraction_jobs", "created_measurements")
    _safe_drop_column("extraction_jobs", "total_received")
    _safe_drop_column("extraction_jobs", "error_message")
    _safe_drop_column("extraction_jobs", "corpus_id")
    _safe_drop_column("extraction_jobs", "source_type")
    _safe_drop_column("extraction_jobs", "source_reference")