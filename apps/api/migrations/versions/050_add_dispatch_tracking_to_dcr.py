"""Add dispatch tracking columns to data_collection_requests (NFM-2645).

Adds four nullable columns for tracking gap-fill dispatch lifecycle:

  - dispatched_at:     when the request was dispatched
  - dispatched_path:   which fill path was chosen (literature / dft / external_db / cascade)
  - dispatch_status:   dispatch outcome (pending / running / success / failed)
  - result_reference:  celery_task_id / external_ref / etc.

All columns are nullable for backward compatibility — existing rows are
unaffected.  An index on dispatch_status supports the primary query
filter (see ADR-NFM-2577).

Reversible: downgrade() drops all four columns and the index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "050_add_dispatch_tracking_to_dcr"
down_revision: str | Sequence[str] | None = "049_add_ontology_version_to_extraction_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dispatch tracking columns and status index to data_collection_requests."""
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the request was dispatched to a fill path.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatched_path",
            sa.String(50),
            nullable=True,
            comment="Fill path chosen: literature / dft / external_db / cascade.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatch_status",
            sa.String(20),
            nullable=True,
            comment="Dispatch outcome: pending / running / success / failed.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "result_reference",
            sa.String(500),
            nullable=True,
            comment="External reference: celery_task_id / external_ref / etc.",
        ),
    )

    # Index for primary query filter on dispatch_status
    op.create_index(
        "ix_dcr_dispatch_status",
        "data_collection_requests",
        ["dispatch_status"],
    )


def downgrade() -> None:
    """Remove dispatch tracking columns and status index."""
    op.drop_index("ix_dcr_dispatch_status", table_name="data_collection_requests")
    op.drop_column("data_collection_requests", "result_reference")
    op.drop_column("data_collection_requests", "dispatch_status")
    op.drop_column("data_collection_requests", "dispatched_path")
    op.drop_column("data_collection_requests", "dispatched_at")
