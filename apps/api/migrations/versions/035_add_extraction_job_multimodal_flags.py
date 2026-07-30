"""Add multimodal-flag columns to extraction_jobs (NFM-2137)

Revision ID: 035_add_extraction_job_multimodal_flags
Revises: 034_add_extraction_job_persistence_columns
Create Date: 2026-07-30

NFM-2137 — Close the second schema gap left by the NFM-2013 hotfix.
Migration 034 (NFM-2115) added the 13 provenance/status/count/timestamp
columns that ``ingest_extraction_batch`` writes via SQLAlchemy defaults,
but missed the 4 multimodal-flag columns preserved from the original
``ExtractionJob`` stub. Those columns are declared in
``apps/api/src/nfm_db/models/extraction_job.py`` (lines 106-112):

    extract_figures:      Mapped[bool]              default=False
    extract_tables:       Mapped[bool]              default=False
    confidence_threshold: Mapped[float]             default=0.5
    figure_types:         Mapped[list[str] | None]  JSONArray, nullable=True

The running prod image's INSERT therefore crashes with::

    asyncpg.UndefinedColumnError:
        column "extract_figures" of relation "extraction_jobs" does not exist

This migration chains off migration 034 and adds the 4 missing columns.
Every ``ADD COLUMN`` uses ``IF NOT EXISTS`` semantics on PostgreSQL so the
migration is idempotent and safe to re-run against a partially-migrated
DB (e.g. if a future hotfix adds a subset of these columns first). The
SQLite test path uses ``op.add_column`` with explicit ``server_default``
on the NOT NULL columns (so any legacy rows inserted before 034+035
receive a valid default during column addition) and tolerates the
``OperationalError`` raised when the column already exists.

Columns added (mirroring ``apps/api/src/nfm_db/models/extraction_job.py``):

Multimodal flags (extraction_jobs stub preserved across NFM-2013 hotfix)
  * extract_figures      BOOLEAN          NOT NULL DEFAULT FALSE
  * extract_tables       BOOLEAN          NOT NULL DEFAULT FALSE
  * confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.5
  * figure_types         JSONB            NULL

Implementation note:
  Same hand-rolled DDL pattern as migration 034 — raw ``ALTER TABLE``
  strings on PG, ``op.add_column`` with ``server_default`` on SQLite.
  This keeps both branches independently testable and avoids the
  ``Column.compile(dialect=...)`` foot-gun that bit migration 034 v1.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035_add_extraction_job_multimodal_flags"
down_revision: str | Sequence[str] | None = "034_add_extraction_job_persistence_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Column specs. Each entry is the literal ``ALTER TABLE extraction_jobs
# ADD COLUMN IF NOT EXISTS ...`` fragment emitted on PostgreSQL. The SQLite
# path uses the equivalent ``op.add_column(...)`` with hand-built Column
# objects (so test-DDL parity still holds but the SQLite and PG paths
# remain fully independent).
# ---------------------------------------------------------------------------

# PG DDL — derived directly from apps/api/src/nfm_db/models/extraction_job.py.
# Note: PostgreSQL ``ALTER TABLE ... ADD COLUMN`` is metadata-only in PG 11+
# even for ``NOT NULL DEFAULT`` when the default is a constant, so this is
# safe on the running pgvector/pgvector:pg16 image. The DEFAULT clauses match
# the Python-side ``default=`` arguments on the SQLAlchemy mapped columns, so
# legacy rows (inserted before this migration ran) receive the same values
# the running API would have written.
_PG_ADD_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS extract_figures BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS extract_tables BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.5",
    "ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS figure_types JSONB",
)

# Down migration: drop the same 4 columns. Order does not matter (no FKs
# link these columns), but we list them in reverse-add order for hygiene.
_PG_DROP_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS figure_types",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS confidence_threshold",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS extract_tables",
    "ALTER TABLE extraction_jobs DROP COLUMN IF EXISTS extract_figures",
)

# SQLite specs: same column shape, expressed as ``op.add_column`` calls.
# ``op.add_column`` accepts the full Column object, so the NOT NULL
# boolean / float columns carry an explicit ``server_default`` so any
# legacy rows inserted before migration 035 receive a valid default
# during column addition — keeping the NOT NULL constraint satisfiable
# for backfilled data. ``figure_types`` is nullable and has no default,
# matching the SQLAlchemy model (``JSONArray, default=None, nullable=True``).
import sqlalchemy as sa  # noqa: E402  (post-typing-import to keep PG block first)

_SQLITE_ADD_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("extract_figures", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    sa.Column("extract_tables", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.5"),
    sa.Column("figure_types", sa.JSON(), nullable=True),
)


def upgrade() -> None:
    """Add 4 multimodal-flag columns to extraction_jobs.

    Uses raw PG ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` (consistent with
    migration 034) and ``op.add_column(...)`` on SQLite. Both branches are
    independently idempotent: ``IF NOT EXISTS`` on PG, ``OperationalError``
    swallow on SQLite.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        for stmt in _PG_ADD_COLUMNS:
            op.execute(stmt)
        return
    # SQLite (test path): ``ADD COLUMN IF NOT EXISTS`` does not exist; rely
    # on ``op.add_column`` and tolerate ``OperationalError`` so the test
    # suite can re-run the migration. ``server_default`` on each NOT NULL
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
    """Drop the 4 columns added by upgrade(). Idempotent on PG and SQLite."""
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
