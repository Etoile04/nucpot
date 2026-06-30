"""Add quality-gate and v4 workflow columns to _ref_gap_fill_staging.

The ORM model (ref_gap_fill.py) references dedup_hash, range_validated,
fill_batch_id, review_note, reviewer_id, reviewed_at, promoted_to_pm_id,
and promoted_at — but the original table creation migration
(b5f3a2c1d8e0) did not include them. This migration adds the missing
columns so the extraction pipeline can stage records.

Relates to: NFM-567 (E2E extraction fix)

Revision ID: 007
Revises: 006
Create Date: 2026-06-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str | Sequence[str] | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "_ref_gap_fill_staging"


def upgrade() -> None:
    """Add quality-gate and v4 workflow columns."""

    # --- Critical: unblocks extraction pipeline staging step ---
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dedup_hash VARCHAR(64) NOT NULL DEFAULT ''"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "range_validated BOOLEAN NOT NULL DEFAULT TRUE"
    )

    # --- fill_batch_id: rename from batch_id + change type to UUID ---
    # The original migration created batch_id VARCHAR(100).
    # The ORM model now uses fill_batch_id UUID.
    col_exists = op.get_bind().scalar(
        f"SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_name='{TABLE}' AND column_name='batch_id')"
    )
    if col_exists:
        op.execute(
            f"ALTER TABLE {TABLE} RENAME COLUMN batch_id TO fill_batch_id"
        )
        op.execute(
            f"ALTER TABLE {TABLE} ALTER COLUMN fill_batch_id TYPE UUID "
            "USING fill_batch_id::uuid"
        )
    else:
        col_exists_new = op.get_bind().scalar(
            f"SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_name='{TABLE}' AND column_name='fill_batch_id')"
        )
        if not col_exists_new:
            op.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN fill_batch_id UUID"
            )

    # --- Review workflow columns ---
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "reviewer_id UUID"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "reviewed_at TIMESTAMPTZ"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "promoted_to_pm_id UUID"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "promoted_at TIMESTAMPTZ"
    )

    # Fix column name: original migration uses review_notes, model uses review_note
    review_notes_exists = op.get_bind().scalar(
        f"SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_name='{TABLE}' AND column_name='review_notes')"
    )
    review_note_exists = op.get_bind().scalar(
        f"SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_name='{TABLE}' AND column_name='review_note')"
    )
    if review_notes_exists and not review_note_exists:
        op.execute(
            f"ALTER TABLE {TABLE} RENAME COLUMN review_notes TO review_note"
        )
    elif not review_note_exists and not review_notes_exists:
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN review_note TEXT"
        )

    # --- Indexes ---
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_staging_dedup ON {TABLE} (dedup_hash)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_staging_fill_batch ON {TABLE} (fill_batch_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop "
        f"ON {TABLE} (element_system, phase, property_name)"
    )


def downgrade() -> None:
    """Remove quality-gate and v4 workflow columns."""
    op.execute(f"DROP INDEX IF EXISTS idx_staging_fill_batch")
    op.execute(f"DROP INDEX IF EXISTS idx_staging_dedup")
    op.execute(f"DROP INDEX IF EXISTS idx_staging_element_phase_prop")

    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS promoted_at")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS promoted_to_pm_id")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS reviewed_at")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS reviewer_id")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS review_note")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS range_validated")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS dedup_hash")
    # Note: fill_batch_id rename is not reversed (lossy on type change)
