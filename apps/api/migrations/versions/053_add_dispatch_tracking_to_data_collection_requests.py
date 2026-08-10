"""Add dispatch tracking columns to data_collection_requests (NFM-2659).

Adds 4 nullable columns:
- dispatched_at (DateTime(tz), nullable)
- dispatch_status (String(20), nullable) — running | success | failed
- dispatched_path (String(200), nullable) — gap-fill path name
- result_reference (String(500), nullable) — external reference from fill path

All nullable for backward compatibility. Reversible downgrade drops all 4.

Revision ID: 053_add_dispatch_tracking_to_data_collection_requests
Revises: 052_add_datasource_metadata
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "053_add_dispatch_tracking_to_data_collection_requests"
down_revision: str | None = "052_add_datasource_metadata"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "data_collection_requests",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_collection_requests",
        sa.Column("dispatched_path", sa.String(200), nullable=True),
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
