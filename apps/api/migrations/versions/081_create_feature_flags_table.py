"""081 — internal feature-flag service table (NFM-4180).

Revision ID: 081_create_feature_flags_table
Revises: 080_kg_orphan_bridge_u10mo_u3si_puo2
Create Date: 2026-09-03

NFM-4146-FU2 / NFM-4180 — replace the build-time
``NEXT_PUBLIC_DATA_LOSS_NOTICE`` env-var gate with a runtime feature-flag
service. This migration creates the ``feature_flags`` table and seeds the
single flag the frontend reads today.

Numbering note: this migration was authored as ``071`` on a worktree
branched off ``069`` (before the NFM-4146 merge). Main's head moved on
and ``071`` is already taken by ``071_f4_uuid_titled_source_guard``, so
the port renumbers it to ``081`` and re-chains it onto ``080``.

Idempotent: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING so
re-running against a partially-migrated database is safe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "081_create_feature_flags_table"
down_revision: str | Sequence[str] | None = "080_kg_orphan_bridge_u10mo_u3si_puo2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_flags (
            key VARCHAR(64) PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            rollout_percentage SMALLINT NOT NULL DEFAULT 0
                CONSTRAINT feature_flags_rollout_percentage_range
                CHECK (rollout_percentage BETWEEN 0 AND 100),
            description VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Seed default-off, matching the env-var gate's safe default (NFM-4146).
    # Operators flip it via PUT /api/v1/feature-flags/DATA_LOSS_NOTICE —
    # no redeploy, and rollout_percentage supports the 10% canary cohort
    # for the recast-restored datasets.
    op.execute(
        """
        INSERT INTO feature_flags (key, enabled, rollout_percentage, description)
        VALUES ('DATA_LOSS_NOTICE', FALSE, 0,
                'DataLossNotice banner on the 10 recast-affected datasets (NFM-4134.A)')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feature_flags")
