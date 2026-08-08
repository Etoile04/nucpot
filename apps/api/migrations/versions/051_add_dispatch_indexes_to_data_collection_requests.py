"""Add indexes on dispatch_status and dispatched_path to data_collection_requests (NFM-2653).

The dispatch-tracking columns added by migration 050 are used by the
gap-dispatch router's filter endpoints (list_dispatch_status / retry), and by
the dispatch_batch worker for status-based partitioning. B-tree indexes on
the two most-queried columns make those lookups O(log n) instead of full scans.

  - ix_dcr_dispatch_status:  WHERE dispatch_status = '<value>'
  - ix_dcr_dispatched_path:  WHERE dispatched_path = '<value>'

Both are non-unique partial-coverage indexes. The cardinality of each
column is small (4-5 distinct values), but the table grows per coverage
gap, so the indexes still pay off for batched dispatch queries.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "051_add_dispatch_indexes_to_data_collection_requests"
down_revision: str | Sequence[str] | None = (
    "050_add_dispatch_tracking_to_data_collection_requests"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dispatch filtering indexes to data_collection_requests."""
    op.create_index(
        "ix_dcr_dispatch_status",
        "data_collection_requests",
        ["dispatch_status"],
        unique=False,
    )
    op.create_index(
        "ix_dcr_dispatched_path",
        "data_collection_requests",
        ["dispatched_path"],
        unique=False,
    )


def downgrade() -> None:
    """Drop dispatch filtering indexes from data_collection_requests."""
    op.drop_index("ix_dcr_dispatched_path", table_name="data_collection_requests")
    op.drop_index("ix_dcr_dispatch_status", table_name="data_collection_requests")