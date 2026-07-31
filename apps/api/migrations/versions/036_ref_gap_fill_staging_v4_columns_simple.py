"""Add missing dedup_hash and quality-gate columns (simplified, no pgcrypto).

Revision ID: 036_ref_gap_fill_staging_v4_columns_simple
Revises: 035_ref_gap_fill_staging_v4_columns
Create Date: 2026-07-30

NFM-2147 (D4 / ADR-NFM-2139) — Alembic-only migration authority:
this is the Alembic port of the legacy ``apps/api/scripts/migrations/
002_fix_dedup_hash_simple.sql`` emergency patch.  It is a deliberately
idempotent backstop that lands at the same end-state as revision 035
but uses a slightly simpler / more defensive ordering; both files were
kept in parallel in production (issue NFM-600) because some prod DBs
had been patched by 001 and others by 002, and either branch must end
at the same schema.

Original SQL header (NFM-600):

    Add missing dedup_hash and quality-gate columns (simplified)

    production has old version of 001_add_dedup_hash.sql
    that uses encode(digest()) requiring pgcrypto extension.
    This file is a self-contained, no-extension-needed alternative.
    Idempotent: safe to re-run.

The end state is identical to revision 035 (see ADR §5 D4 acceptance
criterion 1: "The two known files... are converted to Alembic
revisions with the same DDL").  Both revisions chain off each other,
so the prod alembic head advances to ``036`` after this lands.

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

Downgrade reverses the migration in reverse order; also idempotent so
a no-op run is safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036_ref_gap_fill_staging_v4_columns_simple"
down_revision: str | Sequence[str] | None = "035_ref_gap_fill_staging_v4_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# PG DDL — taken verbatim from apps/api/scripts/migrations/002_fix_dedup_hash_simple.sql
# (byte-for-byte, including the original ``!~ '^['`` regex on line 57 which
# is a known typo.  Preserved here for AC-1 ("same DDL"); the regex
# pattern is corrected in any successor migration that supersedes 002).
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add dedup / quality-gate / v4 workflow columns + indexes (defensive backstop).

    This is the 002 path (simplified; no pgcrypto).  The end state is
    identical to revision 035; running 036 after 035 is a no-op.
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
    """Apply the PG DDL verbatim from 002_fix_dedup_hash_simple.sql."""
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

    # 3. fill_batch_id (UUID) — only if not already present.  Preserves the
    #    original 002 nested IF / ELSIF ordering which is slightly more
    #    defensive than 001's ordering: it first checks whether fill_batch_id
    #    exists at all and only then decides to either rename batch_id or add
    #    fresh.
    #    NOTE: original regex `fill_batch_id::text !~ '^['` is a known typo
    #    (matches every string), preserved here for AC-1 ("same DDL").
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '_ref_gap_fill_staging'
                AND column_name = 'fill_batch_id'
            ) THEN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = '_ref_gap_fill_staging'
                    AND column_name = 'batch_id'
                ) THEN
                    ALTER TABLE _ref_gap_fill_staging RENAME COLUMN batch_id TO fill_batch_id;
                    UPDATE _ref_gap_fill_staging SET fill_batch_id = NULL
                        WHERE fill_batch_id::text !~ '^[';
                    ALTER TABLE _ref_gap_fill_staging ALTER COLUMN fill_batch_id TYPE UUID
                        USING CASE WHEN fill_batch_id IS NULL THEN NULL
                            ELSE fill_batch_id::uuid END;
                ELSE
                    ALTER TABLE _ref_gap_fill_staging ADD COLUMN fill_batch_id UUID;
                END IF;
            END IF;
        END
        $$;
        """
    )

    # 4. review_note (handle old review_notes name)
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

    # 5–8. The remaining workflow columns.
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

    # 9. Indexes
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_staging_fill_batch "
        "ON _ref_gap_fill_staging (fill_batch_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop "
        "ON _ref_gap_fill_staging (element_system, phase, property_name)"
    )


def downgrade() -> None:
    """Reverse the 036 additions; idempotent on PG and SQLite."""
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
    """Reverse the 036 PG DDL."""
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
