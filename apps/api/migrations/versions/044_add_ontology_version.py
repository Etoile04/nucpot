"""Create ontology_versions table and seed version 0.1.0 (NFM-2579).

OntologyVersion stores versioned ontology schema snapshots.  Each row
carries a semver version string, a status (draft/published/deprecated),
an optional changelog, the author FK, and the full ontology definition
as a JSONB payload.

Standalone migration from head 042 (parallel with T1).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "044_add_ontology_version"
down_revision: str | Sequence[str] | None = "042_extraction_step_and_chunk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ontology_versions table and seed initial version 0.1.0."""
    op.create_table(
        "ontology_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "version",
            sa.String(50),
            unique=True,
            nullable=False,
            comment="Semver version string, e.g. 1.2.0.",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft | published | deprecated.",
        ),
        sa.Column(
            "changelog",
            sa.Text,
            nullable=True,
            comment="Human-readable changelog.",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="User who created this ontology version.",
        ),
        sa.Column(
            "ontology_data",
            sa.dialects.postgresql.JSONB,
            nullable=True,
            comment="The actual ontology schema content as JSON.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ontology_versions_version", "ontology_versions", ["version"])

    # Seed initial version 0.1.0 with published status and empty ontology_data.
    op.execute(
        sa.text(
            """
            INSERT INTO ontology_versions (version, status, changelog, created_by, ontology_data, created_at, updated_at)
            VALUES (
                '0.1.0',
                'published',
                'Initial ontology version.',
                (
                    SELECT id FROM users
                    WHERE email = 'system@nucpot.internal'
                    LIMIT 1
                ),
                '{}'::jsonb,
                NOW(),
                NOW()
            )
            """
        )
    )


def downgrade() -> None:
    """Drop ontology_versions table."""
    op.drop_index("ix_ontology_versions_version", table_name="ontology_versions")
    op.drop_table("ontology_versions")
