"""Add quality-gate and v4 workflow columns to _ref_gap_fill_staging (NFM-567).

Revision ID: 035_ref_gap_fill_staging_v4_columns
Revises: 034_add_extraction_job_persistence_columns
Create Date: 2026-07-30

NFM-2147 (D4 / ADR-NFM-2139) — Alembic-only migration authority:
this is the Alembic port of the legacy ``apps/api/scripts/migrations/
001_add_dedup_hash.sql`` emergency patch.  Once this revision ships (and
its successor 036 lands), the legacy ``for SQL_FILE in ...`` block in
``.github/workflows/production-deployment.yml`` is removed and Alembic
becomes the single source of truth for schema changes.

Original SQL (NFM-567 — discovered during E2E DOI extraction testing):

    The ORM model (models/ref_gap_fill.py) references dedup_hash,
    range_validated, fill_batch_id, reviewer_id, reviewed_at,
    promoted_to_pm_id, promoted_at — but the original table creation
    migration only had batch_id VARCHAR(100).  This causes v4
    extraction jobs to fail at the staging step.

The port is byte-faithful to the original ``001_add_dedup_hash.sql``:
every DDL step is gated by ``information_schema`` checks so the
migration is idempotent on a partially-patched production DB (the
prod DB already has the columns from the earlier ``psql`` runs
NFM-2114 / NFM-2115 audited).

End state (matches ``RefGapFillStaging`` in
``apps/api/src/nfm_db/models/ref_gap_fill.py``):

    Columns added (all conditional on absence):
      * dedup_hash VARCHAR(64) NOT NULL DEFAULT ''
      * range_validated BOOLEAN NOT NULL DEFAULT TRUE
      * fill_batch_id UUID                          (rename of batch_id)
      * review_note TEXT                            (rename of review_notes)
      * reviewer_id UUID
      * reviewed_at TIMESTAMPTZ
      * promoted_to_pm_id UUID
      * promoted_at TIMESTAMPTZ

    Indexes added (CREATE INDEX IF NOT EXISTS):
      * idx_staging_dedup               (dedup_hash)
      * idx_staging_fill_batch          (fill_batch_id)
      * idx_staging_element_phase_prop  (element_system, phase, property_name)

Downgrade reverses the migration: drops the indexes, the new columns,
and is also idempotent so a no-op run is safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035_ref_gap_fill_staging_v4_columns"
down_revision: str | Sequence[str] | None = "034_add_extraction_job_persistence_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# PG DDL — taken verbatim from apps/api/scripts/migrations/001_add_dedup_hash.sql
# (byte-for-byte, including the original ``!~ '^['`` regex on line 54 which
# is a known typo that clears every ``fill_batch_id`` row — preserved here
# for AC-1 ("same DDL") until successor 036 can supersede with a safer regex).
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add dedup / quality-gate / v4 workflow columns + indexes to _ref_gap_fill_staging.

    Idempotent on both PG and SQLite (SQLite path uses op.add_column /
    op.create_index directly because ``ADD COLUMN IF NOT EXISTS`` /
    ``information_schema`` are PG-only).
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        _upgrade_postgres()
        return

    # SQLite (test path): use op.add_column / op.create_index directly.
    import sqlalchemy.exc  # noqa: PLC0415  (SQLite-only branch)

    columns: tuple[sa.Column, ...] = (
        sa.Column("dedup_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "range_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("fill_batch_id", sa.String(36), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_to_pm_id", sa.String(36), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    )
    for col in columns:
        try:
            op.add_column("_ref_gap_fill_staging", col)
        except sqlalchemy.exc.OperationalError:
            # Already present from a prior partial run.
            pass

    indexes: tuple[tuple[str, Sequence[str]], ...] = (
        ("idx_staging_dedup", ("dedup_hash",)),
        ("idx_staging_fill_batch", ("fill_batch_id",)),
        ("idx_staging_element_phase_prop", ("element_system", "phase", "property_name")),
    )
    for name, cols in indexes:
        try:
            op.create_index(name, "_ref_gap_fill_staging", list(cols))
        except sqlalchemy.exc.OperationalError:
            pass


def _upgrade_postgres() -> None:
    """Apply the PG DDL verbatim from 001_add_dedup_hash.sql."""
    # 1. dedup_hash (critical: unblocks extraction pipeline staging)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'dedup_hash'
            ) THEN
                ALTER TABLE _ref_gap_fill_staging
                    ADD COLUMN dedup_hash VARCHAR(64) NOT NULL DEFAULT '';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_staging_dedup "
        "ON _ref_gap_fill_staging (dedup_hash)"
    )

    # 2. range_validated
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'range_validated'
            ) THEN
                ALTER TABLE _ref_gap_fill_staging
                    ADD COLUMN range_validated BOOLEAN NOT NULL DEFAULT TRUE;
            END IF;
        END
        $$;
        """
    )

    # 3. Rename batch_id → fill_batch_id (VARCHAR → UUID)
    #    NOTE: original regex `fill_batch_id::text !~ '^['` is a known typo
    #    (matches every string), preserved here for AC-1 ("same DDL").
    #    Revision 036 supersedes this with a safer regex.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'batch_id'
            ) THEN
                ALTER TABLE _ref_gap_fill_staging RENAME COLUMN batch_id TO fill_batch_id;
                UPDATE _ref_gap_fill_staging SET fill_batch_id = NULL WHERE fill_batch_id::text !~ '^[';
                ALTER TABLE _ref_gap_fill_staging ALTER COLUMN fill_batch_id TYPE UUID
                    USING CASE WHEN fill_batch_id IS NULL THEN NULL ELSE fill_batch_id::uuid END;
            ELSIF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'fill_batch_id'
            ) THEN
                ALTER TABLE _ref_gap_fill_staging ADD COLUMN fill_batch_id UUID;
            END IF;
        END
        $$;
        """
    )

    # 4. review workflow columns (rename review_notes → review_note if old
    #    name exists, otherwise add fresh).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'review_note'
            ) THEN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = '_ref_gap_fill_staging'
                    AND column_name = 'review_notes'
                ) THEN
                    ALTER TABLE _ref_gap_fill_staging RENAME COLUMN review_notes TO review_note;
                ELSE
                    ALTER TABLE _ref_gap_fill_staging ADD COLUMN review_note TEXT;
                END IF;
            END IF;
        END
        $$;
        """
    )

    for column_name, ddl_type in (
        ("reviewer_id", "UUID"),
        ("reviewed_at", "TIMESTAMPTZ"),
        ("promoted_to_pm_id", "UUID"),
        ("promoted_at", "TIMESTAMPTZ"),
    ):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = '_ref_gap_fill_staging' AND column_name = '{column_name}') THEN
                    ALTER TABLE _ref_gap_fill_staging ADD COLUMN {column_name} {ddl_type};
                END IF;
            END
            $$;
            """
        )

    # 5. Indexes
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_staging_fill_batch "
        "ON _ref_gap_fill_staging (fill_batch_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop "
        "ON _ref_gap_fill_staging (element_system, phase, property_name)"
    )


def downgrade() -> None:
    """Reverse the 035 additions; idempotent on PG and SQLite."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        _downgrade_postgres()
        return

    import sqlalchemy.exc  # noqa: PLC0415  (SQLite-only branch)

    for name in (
        "idx_staging_element_phase_prop",
        "idx_staging_fill_batch",
        "idx_staging_dedup",
    ):
        try:
            op.drop_index(name, table_name="_ref_gap_fill_staging")
        except sqlalchemy.exc.OperationalError:
            pass
    for col in (
        "promoted_at",
        "promoted_to_pm_id",
        "reviewed_at",
        "reviewer_id",
        "review_note",
        "range_validated",
        "dedup_hash",
    ):
        try:
            op.drop_column("_ref_gap_fill_staging", col)
        except sqlalchemy.exc.OperationalError:
            pass


def _downgrade_postgres() -> None:
    """Reverse the 035 PG DDL."""
    op.execute("DROP INDEX IF EXISTS idx_staging_element_phase_prop")
    op.execute("DROP INDEX IF EXISTS idx_staging_fill_batch")
    op.execute("DROP INDEX IF EXISTS idx_staging_dedup")

    for column_name in (
        "promoted_at",
        "promoted_to_pm_id",
        "reviewed_at",
        "reviewer_id",
        "review_note",
        "range_validated",
        "dedup_hash",
    ):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = '_ref_gap_fill_staging' AND column_name = '{column_name}') THEN
                    ALTER TABLE _ref_gap_fill_staging DROP COLUMN {column_name};
                END IF;
            END
            $$;
            """
        )
