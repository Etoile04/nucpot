"""Create extraction_gaps table (NFM-2575-T1).

Persistent gap tracking for ontology-driven extraction.  Each row
represents a missing data point for a specific (entity_type, property)
pair within an ontology version, optionally linked to the extraction
chunk being processed.

Status lifecycle: open → filling → filled | wont_fix.

Foreign keys:
  - ontology_versions.id (CASCADE delete)
  - extraction_chunks.id (SET NULL on delete)

Indexes:
  - (ontology_version_id, entity_type, property) UNIQUE — prevent duplicate gaps
  - chunk_id — chunk-based gap lookup
  - gap_status — status filtering
  - ontology_version_id — recall metrics query
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "047_extraction_gap"
down_revision: str | Sequence[str] | None = "046_add_knowledge_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_gaps",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
        ),
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_versions.id", ondelete="CASCADE"),
            nullable=False,
            comment="Ontology version that defines the expected schema.",
        ),
        sa.Column(
            "entity_type",
            sa.String(100),
            nullable=False,
            comment="Entity type, e.g. NuclearMaterial, Isotope.",
        ),
        sa.Column(
            "property",
            sa.String(200),
            nullable=False,
            comment="Property name, e.g. density, half_life.",
        ),
        sa.Column(
            "source_reference",
            sa.Text(),
            nullable=True,
            comment="Source identifier where the gap was detected.",
        ),
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("extraction_chunks.id", ondelete="SET NULL"),
            nullable=True,
            comment="Extraction chunk being processed when gap was found.",
        ),
        sa.Column(
            "gap_status",
            sa.String(20),
            nullable=False,
            server_default="open",
            comment="open | filling | filled | wont_fix",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the gap was first detected.",
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the gap was filled or marked wont_fix.",
        ),
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

    # Composite unique constraint: one gap per (ontology_version, entity_type, property)
    op.create_index(
        "ix_extraction_gaps_ov_entity_property",
        "extraction_gaps",
        ["ontology_version_id", "entity_type", "property"],
        unique=True,
    )
    # Chunk-based gap lookup
    op.create_index(
        "ix_extraction_gaps_chunk_id",
        "extraction_gaps",
        ["chunk_id"],
    )
    # Status filtering
    op.create_index(
        "ix_extraction_gaps_gap_status",
        "extraction_gaps",
        ["gap_status"],
    )
    # Recall metrics query
    op.create_index(
        "ix_extraction_gaps_ontology_version_id",
        "extraction_gaps",
        ["ontology_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_gaps_ontology_version_id", table_name="extraction_gaps")
    op.drop_index("ix_extraction_gaps_gap_status", table_name="extraction_gaps")
    op.drop_index("ix_extraction_gaps_chunk_id", table_name="extraction_gaps")
    op.drop_index("ix_extraction_gaps_ov_entity_property", table_name="extraction_gaps")
    op.drop_table("extraction_gaps")
