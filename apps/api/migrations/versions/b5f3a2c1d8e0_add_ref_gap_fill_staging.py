"""add _ref_gap_fill_staging table

Revision ID: b5f3a2c1d8e0
Revises: d3ddb691ae20
Create Date: 2026-06-11 17:45:00.000000

Per NFM-54 design Section 1.2: staging table for reference gap-fill
ingestion pipeline. Every incoming reference_value lands here first,
passes quality gates, then gets promoted to property_measurements.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5f3a2c1d8e0"
down_revision: Union[str, Sequence[str], None] = "d3ddb691ae20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create _ref_gap_fill_staging table with enums and indexes."""
    confidence_enum = sa.Enum(
        "high",
        "medium",
        "low",
        name="confidence_enum",
    )
    staging_status_enum = sa.Enum(
        "pending",
        "approved",
        "rejected",
        "promoted",
        name="staging_status_enum",
    )
    cache_level_enum = sa.Enum(
        "L1",
        "L2",
        "L3A",
        "L3B",
        name="cache_level_enum",
    )

    confidence_enum.create(op.get_bind(), checkfirst=True)
    staging_status_enum.create(op.get_bind(), checkfirst=True)
    cache_level_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "_ref_gap_fill_staging",
        # Primary key
        sa.Column("id", sa.Uuid(), primary_key=True),

        # --- Source fields (verbatim from nfm-ref-gapfill) ---
        sa.Column("element_system", sa.String(50), nullable=False),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("property_name", sa.String(100), nullable=False),
        sa.Column("value", sa.DoublePrecision(), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("method", sa.String(100), nullable=True),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("source_doi", sa.String(200), nullable=True),
        sa.Column("uncertainty", sa.DoublePrecision(), nullable=True),
        sa.Column("temperature", sa.DoublePrecision(), nullable=True),

        # --- Quality gate columns ---
        sa.Column(
            "confidence",
            confidence_enum,
            nullable=False,
            server_default="medium",
        ),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column(
            "range_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),

        # --- Review workflow ---
        sa.Column(
            "status",
            staging_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column(
            "reviewer_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),

        # --- Promotion tracking ---
        sa.Column("promoted_to_pm_id", sa.Uuid(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),

        # --- Metadata ---
        sa.Column("cache_level", cache_level_enum, nullable=True),
        sa.Column("fill_batch_id", sa.Uuid(), nullable=True),

        # --- Timestamps ---
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Indexes per NFM-54 design Section 1.2
    op.create_index(
        "idx_staging_status",
        "_ref_gap_fill_staging",
        ["status"],
    )
    op.create_index(
        "idx_staging_element_phase_prop",
        "_ref_gap_fill_staging",
        ["element_system", "phase", "property_name"],
    )
    op.create_index(
        "idx_staging_dedup",
        "_ref_gap_fill_staging",
        ["dedup_hash"],
    )
    op.create_index(
        "idx_staging_needs_review",
        "_ref_gap_fill_staging",
        ["status"],
        postgresql_where="status = 'pending'",
    )
    op.create_index(
        "idx_staging_fill_batch",
        "_ref_gap_fill_staging",
        ["fill_batch_id"],
    )


def downgrade() -> None:
    """Drop _ref_gap_fill_staging table and enums."""
    op.drop_table("_ref_gap_fill_staging")

    op.execute("DROP TYPE IF EXISTS cache_level_enum")
    op.execute("DROP TYPE IF EXISTS staging_status_enum")
    op.execute("DROP TYPE IF EXISTS confidence_enum")
