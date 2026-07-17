"""add entity_merge_log table

Revision ID: 013
Revises: 012
Create Date: 2026-07-14 10:00:00.000000

NFM-1391 (B3.1.1): audit-log table for the entity dedup engine.

Records every decision the dedup engine makes when it folds a
duplicate material into a canonical material, so reviewers can
audit, replay, or reverse merges.

Columns:
    id           UUID PK, default gen_random_uuid()
    canonical_id UUID NOT NULL -> materials.id (ON DELETE RESTRICT)
    merged_id    UUID NOT NULL -> materials.id (ON DELETE RESTRICT)
    match_score  DOUBLE PRECISION NOT NULL
    match_method match_method_enum NOT NULL ('exact' | 'fuzzy' | 'semantic')
    merged_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    details      JSONB NULL

Indexes:
    ix_entity_merge_log_canonical   - lookups by survivor material
    ix_entity_merge_log_merged      - lookups by absorbed duplicate
    ix_entity_merge_log_method_score - composite (method, score DESC)
                                      for "recent fuzzy merges with high
                                      confidence" queries
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013a"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create entity_merge_log table, enum, and lookup indexes."""
    op.execute("CREATE TYPE match_method_enum AS ENUM ('exact', 'fuzzy', 'semantic')")

    op.execute("""
        CREATE TABLE entity_merge_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_id UUID NOT NULL
                REFERENCES materials(id) ON DELETE RESTRICT,
            merged_id UUID NOT NULL
                REFERENCES materials(id) ON DELETE RESTRICT,
            match_score DOUBLE PRECISION NOT NULL,
            match_method match_method_enum NOT NULL,
            merged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            details JSONB,

            CONSTRAINT ck_entity_merge_log_score_range
                CHECK (match_score >= 0.0 AND match_score <= 1.0),
            CONSTRAINT ck_entity_merge_log_distinct_ids
                CHECK (canonical_id <> merged_id)
        )
    """)

    op.execute("CREATE INDEX ix_entity_merge_log_canonical ON entity_merge_log (canonical_id)")
    op.execute("CREATE INDEX ix_entity_merge_log_merged ON entity_merge_log (merged_id)")
    op.execute(
        "CREATE INDEX ix_entity_merge_log_method_score "
        "ON entity_merge_log (match_method, match_score DESC)"
    )


def downgrade() -> None:
    """Drop entity_merge_log table and enum."""
    op.execute("DROP INDEX IF EXISTS ix_entity_merge_log_method_score")
    op.execute("DROP INDEX IF EXISTS ix_entity_merge_log_merged")
    op.execute("DROP INDEX IF EXISTS ix_entity_merge_log_canonical")
    op.execute("DROP TABLE IF EXISTS entity_merge_log")
    op.execute("DROP TYPE IF EXISTS match_method_enum")
