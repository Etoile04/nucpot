"""Create re_extraction_queue table (NFM-2581 / NFM-2573-T4).

Queue entries for re-extraction jobs triggered when an ontology version
upgrades.  Each row pairs a corpus with a target ontology version and
tracks the job lifecycle (pending → running → completed | failed |
cancelled).

Foreign keys:
  - ontology_versions.id (CASCADE delete)
  - corpus.id (CASCADE delete)
  - users.id (SET NULL on delete)

Indexes on ontology_version_id and status for common query patterns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "045_add_re_extraction_queue"
down_revision: str | Sequence[str] | None = "044_add_ontology_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "re_extraction_queue",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "ontology_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ontology_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "corpus_id",
            UUID(as_uuid=True),
            sa.ForeignKey("corpus.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggered_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
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

    op.create_index(
        "ix_re_extraction_queue_ontology_version_id",
        "re_extraction_queue",
        ["ontology_version_id"],
    )
    op.create_index(
        "ix_re_extraction_queue_status",
        "re_extraction_queue",
        ["status"],
    )
    op.create_index(
        "ix_re_extraction_queue_corpus_id",
        "re_extraction_queue",
        ["corpus_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_re_extraction_queue_corpus_id", table_name="re_extraction_queue")
    op.drop_index("ix_re_extraction_queue_status", table_name="re_extraction_queue")
    op.drop_index("ix_re_extraction_queue_ontology_version_id", table_name="re_extraction_queue")
    op.drop_table("re_extraction_queue")
