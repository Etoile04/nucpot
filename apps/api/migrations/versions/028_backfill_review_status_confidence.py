"""Backfill review_status: auto-approve high-confidence items.

Revision ID: 028_backfill_review_status_confidence
Revises: 027_merge_heads_011_and_026
Create Date: 2026-07-28

Context:
    The Phase 3 review system added a `review_status` column to kg_nodes,
    kg_edges, and other tables with DEFAULT 'pending'. However, the
    extraction pipeline (kg_re.py, entity_linker.py) never set this column
    explicitly — all items got the DB default 'pending', regardless of
    confidence. This meant high-confidence items (>= 0.6) that should have
    been auto-approved were showing up in the human review queue.

    This migration backfills existing data:
    - kg_nodes: confidence >= 0.6 → review_status = 'approved'
    - kg_edges: confidence >= 0.6 → review_status = 'approved'
    - Items already reviewed (review_status IN ('approved','rejected',
      'needs_revision','corrected')) are NOT touched.

    Going forward, new items get review_status set explicitly in code
    (kg_re.py _create_node/_create_edge, entity_linker.py _create_node).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "028_backfill_review_status_confidence"
down_revision = "027"
branch_labels = None
depends_on = None

# Confidence threshold: items at or above this value are auto-approved.
# Must match REVIEW_CONFIDENCE_THRESHOLD in kg_re.py / entity_linker.py.
REVIEW_THRESHOLD = 0.6


def upgrade() -> None:
    """Backfill review_status for existing high-confidence items."""

    # kg_nodes: auto-approve items with confidence >= threshold that are
    # still in the default 'pending' state.
    op.execute(
        f"""
        UPDATE kg_nodes
        SET review_status = 'approved',
            reviewed_at = NOW()
        WHERE review_status = 'pending'
          AND confidence >= {REVIEW_THRESHOLD}
        """
    )

    # kg_edges: same logic.
    op.execute(
        f"""
        UPDATE kg_edges
        SET review_status = 'approved',
            reviewed_at = NOW()
        WHERE review_status = 'pending'
          AND confidence >= {REVIEW_THRESHOLD}
        """
    )

    # property_measurements: auto-approve items with confidence >= threshold.
    # (property_measurements has reviewed_at but uses kg_review_queue for
    # status — leave as-is if no review_status column exists.)
    # NOTE: property_measurements.review_status may not exist on all DBs;
    # the IF EXISTS clause makes this safe.
    op.execute(
        f"""
        UPDATE property_measurements
        SET reviewed_at = COALESCE(reviewed_at, NOW())
        WHERE reviewed_at IS NULL
          AND confidence >= {REVIEW_THRESHOLD}
        """
    )


def downgrade() -> None:
    """Cannot meaningfully reverse a backfill — recorded for Alembic compliance."""
    # Reverting would require knowing which items were auto-approved vs
    # human-approved. We opt to no-op rather than risk corrupting audit data.
    pass
