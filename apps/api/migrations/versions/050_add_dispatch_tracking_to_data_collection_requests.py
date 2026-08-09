"""Add dispatch tracking columns to data_collection_requests.

Adds 4 nullable columns:
- dispatched_at (DateTime(tz), nullable)
- dispatched_path (String(50), nullable) — literature/dft/external_db/cascade
- dispatch_status (String(20), nullable) — pending/running/success/failed
- result_reference (String(500), nullable) — celery_task_id/external_ref

All nullable for backward compatibility. Reversible downgrade drops all 4.

Revision ID: 050_add_dispatch_tracking
Revises: 049_add_ontology_version_to_extraction_job
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "050_add_dispatch_tracking"
down_revision: str | None = "049_add_ontology_version_to_extraction_job"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "data_collection_requests",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column("dispatched_path", sa.String(50), nullable=True),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column("dispatch_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column("result_reference", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_collection_requests", "result_reference")
    op.drop_column("data_collection_requests", "dispatch_status")
    op.drop_column("data_collection_requests", "dispatched_path")
    op.drop_column("data_collection_requests", "dispatched_at")
