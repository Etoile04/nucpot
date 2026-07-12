"""add _ref_gap_fill_staging table

Revision ID: b5f3a2c1d8e0
Revises: d3ddb691ae20
Create Date: 2026-06-11 17:45:00.000000

Per NFM-54 design Section 1.2: staging table for reference gap-fill
ingestion pipeline.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5f3a2c1d8e0"
down_revision: str | Sequence[str] | None = "d3ddb691ae20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create _ref_gap_fill_staging table with enums and indexes."""
    op.execute("CREATE TYPE confidence_enum AS ENUM (\'high\', \'medium\', \'low\')")
    op.execute("CREATE TYPE staging_status_enum AS ENUM (\'pending\', \'approved\', \'rejected\', \'promoted\')")
    op.execute("CREATE TYPE cache_level_enum AS ENUM (\'L1\', \'L2\', \'L3A\', \'L3B\')")

    op.execute("""
        CREATE TABLE _ref_gap_fill_staging (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            element_system VARCHAR(50) NOT NULL,
            phase VARCHAR(50),
            property_name VARCHAR(100) NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            unit VARCHAR(50) NOT NULL,
            method VARCHAR(100),
            source VARCHAR(200) NOT NULL,
            source_doi VARCHAR(200),
            uncertainty DOUBLE PRECISION,
            temperature DOUBLE PRECISION,
            confidence confidence_enum DEFAULT \'medium\',
            staging_status staging_status_enum DEFAULT \'pending\',
            cache_level cache_level_enum,
            review_notes TEXT,
            batch_id VARCHAR(100),
            imported_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_staging_status ON _ref_gap_fill_staging (staging_status)")
    op.execute("CREATE INDEX idx_staging_element_system ON _ref_gap_fill_staging (element_system)")
    op.execute("CREATE INDEX idx_staging_property ON _ref_gap_fill_staging (property_name)")


def downgrade() -> None:
    """Drop staging table and enums."""
    op.execute("DROP TABLE IF EXISTS _ref_gap_fill_staging")
    op.execute("DROP TYPE IF EXISTS cache_level_enum")
    op.execute("DROP TYPE IF EXISTS staging_status_enum")
    op.execute("DROP TYPE IF EXISTS confidence_enum")
