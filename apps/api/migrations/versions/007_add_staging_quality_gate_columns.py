"""Add quality-gate and v4 workflow columns to _ref_gap_fill_staging.

The ORM model (ref_gap_fill.py) references dedup_hash, range_validated,
fill_batch_id, review_note, reviewer_id, reviewed_at, promoted_to_pm_id,
and promoted_at — but the original table creation migration
(b5f3a2c1d8e0) did not include them. This migration adds the missing
columns so the extraction pipeline can stage records.

Relates to: NFM-567 (E2E extraction fix)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | Sequence[str] | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "_ref_gap_fill_staging"


def _column_exists(connection: object, name: str) -> bool:
    """Return True when ``name`` is a column on ``TABLE``."""
    return bool(
        connection.execute(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                f"WHERE table_name='{TABLE}' AND column_name='{name}')"
            )
        ).scalar()
    )


def upgrade() -> None:
    """Add quality-gate and v4 workflow columns."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
            "dedup_hash VARCHAR(64) NOT NULL DEFAULT ''"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
            "range_validated BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )

    if _column_exists(bind, "batch_id"):
        bind.execute(
            sa.text(f"ALTER TABLE {TABLE} RENAME COLUMN batch_id TO fill_batch_id")
        )
        bind.execute(
            sa.text(
                f"ALTER TABLE {TABLE} ALTER COLUMN fill_batch_id TYPE UUID "
                "USING fill_batch_id::uuid"
            )
        )
    elif not _column_exists(bind, "fill_batch_id"):
        bind.execute(sa.text(f"ALTER TABLE {TABLE} ADD COLUMN fill_batch_id UUID"))

    for ddl in (
        "ADD COLUMN IF NOT EXISTS reviewer_id UUID",
        "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
        "ADD COLUMN IF NOT EXISTS promoted_to_pm_id UUID",
        "ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ",
    ):
        bind.execute(sa.text(f"ALTER TABLE {TABLE} {ddl}"))

    if _column_exists(bind, "review_notes") and not _column_exists(bind, "review_note"):
        bind.execute(sa.text(f"ALTER TABLE {TABLE} RENAME COLUMN review_notes TO review_note"))
    elif not _column_exists(bind, "review_note"):
        bind.execute(sa.text(f"ALTER TABLE {TABLE} ADD COLUMN review_note TEXT"))

    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_staging_dedup ON {TABLE} (dedup_hash)",
        "CREATE INDEX IF NOT EXISTS idx_staging_fill_batch ON {TABLE} (fill_batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop "
        "ON {TABLE} (element_system, phase, property_name)",
    ):
        bind.execute(sa.text(ddl.format(TABLE=TABLE)))


def downgrade() -> None:
    """Remove quality-gate and v4 workflow columns."""
    op.execute("DROP INDEX IF EXISTS idx_staging_fill_batch")
    op.execute("DROP INDEX IF EXISTS idx_staging_dedup")
    op.execute("DROP INDEX IF EXISTS idx_staging_element_phase_prop")

    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS promoted_at")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS promoted_to_pm_id")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS reviewed_at")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS reviewer_id")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS review_note")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS range_validated")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS dedup_hash")
    # Note: fill_batch_id rename is not reversed (lossy on type change)
