"""Create conflict_records table and add default_conflict_strategy (NFM-861)

Revision ID: 014
Revises: 013
Create Date: 2026-07-08

Creates the conflict_records table for multi-source fusion and adds
default_conflict_strategy column to property_types.

Spec references:
  - §6.1: conflict_records table
  - §6.2: default_conflict_strategy column on property_types
  - ADR-NFM-817-3: 4 conflict resolution strategies
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: str | Sequence[str] | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create conflict_records table and add default_conflict_strategy column."""

    # =========================================================================
    # COLUMN: default_conflict_strategy on property_types
    # =========================================================================
    op.execute("""
        ALTER TABLE property_types
        ADD COLUMN IF NOT EXISTS default_conflict_strategy
            VARCHAR(50) DEFAULT 'confidence',
        ADD CONSTRAINT ck_property_types_conflict_strategy
            CHECK (default_conflict_strategy IN (
                'newest', 'confidence', 'consensus', 'manual'
            ))
    """)

    # =========================================================================
    # TABLE: conflict_records
    # =========================================================================
    op.execute("""
        CREATE TABLE conflict_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            material_node_id UUID NOT NULL
                REFERENCES kg_nodes(id) ON DELETE CASCADE,
            property_node_id UUID NOT NULL
                REFERENCES kg_nodes(id) ON DELETE CASCADE,
            property_type_id UUID
                REFERENCES property_types(id) ON DELETE SET NULL,
            conflicting_values JSONB NOT NULL,
            strategy VARCHAR(50) NOT NULL,
            resolved_value JSONB,
            status VARCHAR(20) DEFAULT 'pending',
            resolved_by UUID,
            resolved_at TIMESTAMPTZ,
            resolution_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_conflict_records_strategy
                CHECK (strategy IN (
                    'newest', 'confidence', 'consensus', 'manual'
                )),
            CONSTRAINT ck_conflict_records_status
                CHECK (status IN ('pending', 'resolved', 'escalated'))
        )
    """)

    # =========================================================================
    # INDEXES: conflict_records
    # =========================================================================
    op.execute(
        "CREATE INDEX ix_conflict_records_material "
        "ON conflict_records (material_node_id)"
    )
    op.execute(
        "CREATE INDEX ix_conflict_records_property "
        "ON conflict_records (property_node_id)"
    )
    op.execute(
        "CREATE INDEX ix_conflict_records_status "
        "ON conflict_records (status)"
    )
    op.execute(
        "CREATE INDEX ix_conflict_records_material_property "
        "ON conflict_records (material_node_id, property_node_id)"
    )


def downgrade() -> None:
    """Drop conflict_records table and remove default_conflict_strategy column."""
    op.execute("DROP TABLE IF EXISTS conflict_records CASCADE")
    op.execute("""
        ALTER TABLE property_types
        DROP COLUMN IF EXISTS default_conflict_strategy
    """)
