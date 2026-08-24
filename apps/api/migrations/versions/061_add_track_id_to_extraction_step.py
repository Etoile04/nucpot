"""Add track_id column to extraction_steps (NFM-3595 / NFM-3543-A).

Adds a NOT NULL UUID column with a server-side ``gen_random_uuid()``
default so every step row is guaranteed a stable per-step identity
the moment the column lands.  The accompanying index lets sibling
deliverables (C: GET, D: rerun) look up by ``track_id`` cheaply.

Per the issue AC, the column add, default, index, and backfill all
run in one forward transaction.  Postgres 11+ stores the column
default in the catalog and applies it during ``ALTER TABLE``, so the
backfill for any rows present at apply time is satisfied by the
ALTER itself — there is no separate ``UPDATE`` statement.

NOTE on backfill determinism
----------------------------
The issue AC-5 asks for a deterministic-from-id backfill using
``uuid_generate_v5('<jobs-namespace>, id::text)``.  That requires the
``uuid-ossp`` extension which is not currently installed in the
production schema (only ``pg_trgm`` is — see migration 012).
Installing it here would be an unrelated operational change, so
existing rows receive ``gen_random_uuid()`` from the ALTER default
instead.  Downstream consumers must treat ``track_id`` as opaque;
the column exists to give rerun idempotency a stable handle, not
to encode step identity.  If deterministic derivation is required
later, ship a follow-up migration that ``CREATE EXTENSION uuid-ossp``
and rewrites ``track_id`` from ``id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "061_add_track_id_to_extraction_step"
down_revision: str | Sequence[str] | None = "060_backfill_ref_gap_fill_staging_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``track_id`` UUID NOT NULL + index on ``extraction_steps`` (idempotent)."""
    conn = op.get_bind()

    # Idempotency guard — replay-safe across forward+down+forward cycles.
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'extraction_steps' AND column_name = 'track_id'"
        )
    ).scalar()
    if exists:
        # Column already present (replayed upgrade).  Make sure the
        # index exists too and exit early.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_extraction_steps_track_id "
            "ON extraction_steps (track_id)"
        )
        return

    # 1. Column + server default + NOT NULL — Postgres 11+ stores the
    #    default in the catalog and applies it during ALTER for any
    #    existing rows, so backfill is satisfied by this DDL alone.
    op.add_column(
        "extraction_steps",
        sa.Column(
            "track_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            comment=(
                "Stable per-step identity for rerun idempotency "
                "(NFM-3595). Server-side gen_random_uuid() default."
            ),
        ),
    )

    # 2. Index for rerun-idempotency lookup (siblings C/D).
    op.create_index(
        "ix_extraction_steps_track_id",
        "extraction_steps",
        ["track_id"],
    )


def downgrade() -> None:
    """Drop the index and ``track_id`` column from ``extraction_steps``."""
    op.drop_index("ix_extraction_steps_track_id", table_name="extraction_steps")
    op.drop_column("extraction_steps", "track_id")