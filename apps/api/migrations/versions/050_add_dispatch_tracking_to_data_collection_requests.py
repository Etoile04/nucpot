"""Add dispatch tracking columns to data_collection_requests (NFM-2647).

Adds four nullable columns to support the gap-dispatch router's lifecycle
tracking. These columns are first-class schema fields (not JSONB metadata)
because they must be queryable via indexes for filtering and sorting:

  - dispatched_at:     when the request was dispatched to a fill path
  - dispatched_path:   which fill path (literature / dft / external_db / cascade)
  - dispatch_status:   dispatch lifecycle (pending / running / success / failed)
  - result_reference:  external identifier (celery_task_id, external_ref, etc.)

All columns are nullable — fully backward compatible with existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "050_add_dispatch_tracking_to_data_collection_requests"
down_revision: str | Sequence[str] | None = "049_add_ontology_version_to_extraction_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dispatch tracking columns to data_collection_requests."""
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the request was dispatched to a fill path.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatched_path",
            sa.String(50),
            nullable=True,
            comment="Fill path used: literature / dft / external_db / cascade.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "dispatch_status",
            sa.String(20),
            nullable=True,
            comment="Dispatch lifecycle: pending / running / success / failed.",
        ),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column(
            "result_reference",
            sa.String(500),
            nullable=True,
            comment="External reference (celery_task_id, external_ref, etc.).",
        ),
    )


def downgrade() -> None:
    """Drop dispatch tracking columns from data_collection_requests."""
    op.drop_column("data_collection_requests", "result_reference")
    op.drop_column("data_collection_requests", "dispatch_status")
    op.drop_column("data_collection_requests", "dispatched_path")
    op.drop_column("data_collection_requests", "dispatched_at")
