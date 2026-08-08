"""Create knowledge_gaps table (NFM-2582 / NFM-2573-T5).

Persistent knowledge gap tracking with lifecycle status:
open → in_progress → resolved | wont_fix.

The wont_fix → open auto-reopen is triggered by the gap_reopen_service
when a new ontology version produces extraction results matching a
previously wont_fix gap.

Foreign keys:
  - ontology_versions.id (SET NULL on delete)

Indexes:
  - status (common filter)
  - (gap_type, target_key) UNIQUE — one gap record per type+key
  - ontology_version_id (filter by ontology context)
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "046_add_knowledge_gaps"
down_revision: str | None = "045_add_re_extraction_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "gap_type",
            sa.String(20),
            nullable=False,
            comment="property | entity | relation",
        ),
        sa.Column(
            "target_key",
            sa.String(500),
            nullable=False,
            comment="Canonical key for the missing data point",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="open",
            comment="open | in_progress | resolved | wont_fix",
        ),
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "audit_note",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "resolved_by",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "metadata_",
            sa.Text(),
            nullable=True,
            comment="Flexible JSON metadata",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_kg_status", "knowledge_gaps", ["status"])
    op.create_index(
        "idx_kg_type_target",
        "knowledge_gaps",
        ["gap_type", "target_key"],
        unique=True,
    )
    op.create_index(
        "idx_kg_ontology_version",
        "knowledge_gaps",
        ["ontology_version_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_kg_ontology_version")
    op.drop_index("idx_kg_type_target")
    op.drop_index("idx_kg_status")
    op.drop_table("knowledge_gaps")
