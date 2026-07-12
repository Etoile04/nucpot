"""Review queue service for low-confidence KG items (NFM-859 B2.6).

Provides the business logic for managing the human review queue:
  - Auto-routing low-confidence entities and relations to the queue
  - Listing pending review items with pagination
  - Approving items (promoting them into the live KG)
  - Rejecting items with an optional reason

Items with confidence < LOW_CONFIDENCE_THRESHOLD (0.6) are automatically
routed to ``kg_review_queue`` during extraction or graph building.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, KGReviewQueue
from nfm_db.schemas.nucmat_ontology import LOW_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Add to review queue
# ---------------------------------------------------------------------------


async def add_to_review_queue(
    session: AsyncSession,
    *,
    item_type: str,
    item_id: UUID,
    review_reason: str,
) -> KGReviewQueue:
    """Add an item to the review queue.

    Used by the graph builder when entity/relation confidence is below
    the LOW_CONFIDENCE_THRESHOLD, or when dedup similarity is borderline.

    Returns the created KGReviewQueue record.
    """
    queue_item = KGReviewQueue(
        item_type=item_type,
        item_id=item_id,
        review_reason=review_reason,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add(queue_item)
    await session.flush()
    await session.refresh(queue_item)
    return queue_item


def should_route_to_review(confidence: float) -> bool:
    """Check if an item's confidence is low enough to require review.

    Items with confidence < 0.6 are auto-routed to the review queue.
    """
    return confidence < LOW_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# List pending reviews
# ---------------------------------------------------------------------------


async def list_pending_reviews(
    session: AsyncSession,
    *,
    item_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List pending review queue items with optional type filter.

    Returns a tuple of (items, total_count). Each item is a dict
    with the queue record fields plus associated node/edge data.
    """
    stmt = select(KGReviewQueue).where(KGReviewQueue.status == "pending")

    if item_type is not None:
        stmt = stmt.where(KGReviewQueue.item_type == item_type)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.order_by(KGReviewQueue.created_at).offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        item_data: dict[str, Any] = {
            "id": row.id,
            "item_type": row.item_type,
            "item_id": row.item_id,
            "review_reason": row.review_reason,
            "status": row.status,
            "reviewer_notes": row.reviewer_notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }

        # Attach associated entity data
        if row.item_type == "entity":
            node = await session.get(KGNode, row.item_id)
            if node is not None:
                item_data["entity_data"] = {
                    "node_type": node.node_type,
                    "label": node.label,
                    "confidence": node.confidence,
                    "status": node.status,
                }
        elif row.item_type == "relation":
            edge = await session.get(KGEdge, row.item_id)
            if edge is not None:
                item_data["relation_data"] = {
                    "source_node_id": str(edge.source_node_id),
                    "target_node_id": str(edge.target_node_id),
                    "relation_type": edge.relation_type,
                    "confidence": edge.confidence,
                }

        items.append(item_data)

    return items, total


# ---------------------------------------------------------------------------
# Approve review item
# ---------------------------------------------------------------------------


async def approve_review_item(
    session: AsyncSession,
    *,
    review_id: UUID,
    reviewer_notes: str | None = None,
) -> dict[str, Any]:
    """Approve a pending review item and promote it to the live KG.

    For entities: sets node status to 'active'.
    For relations: no additional action needed (edge already exists).

    Returns the updated review record as a dict.
    """
    queue_item = await session.get(KGReviewQueue, review_id)
    if queue_item is None:
        return {"error": "Review item not found", "status_code": 404}

    if queue_item.status != "pending":
        return {
            "error": f"Item already {queue_item.status}, cannot approve",
            "status_code": 409,
        }

    queue_item.status = "approved"
    queue_item.reviewer_notes = reviewer_notes
    queue_item.reviewed_at = datetime.now(timezone.utc)

    # Promote entity to active status
    if queue_item.item_type == "entity":
        node = await session.get(KGNode, queue_item.item_id)
        if node is not None:
            node.status = "active"
            logger.info(
                "Approved entity %s (%s) — status set to active",
                queue_item.item_id,
                node.label,
            )

    await session.flush()
    await session.refresh(queue_item)

    return {
        "id": queue_item.id,
        "item_type": queue_item.item_type,
        "item_id": queue_item.item_id,
        "status": queue_item.status,
        "reviewer_notes": queue_item.reviewer_notes,
        "reviewed_at": (
            queue_item.reviewed_at.isoformat() if queue_item.reviewed_at else None
        ),
    }


# ---------------------------------------------------------------------------
# Reject review item
# ---------------------------------------------------------------------------


async def reject_review_item(
    session: AsyncSession,
    *,
    review_id: UUID,
    reason: str,
) -> dict[str, Any]:
    """Reject a pending review item with a reason.

    For entities: sets node status to 'deprecated'.
    For relations: deletes the edge from the KG.

    Returns the updated review record as a dict.
    """
    queue_item = await session.get(KGReviewQueue, review_id)
    if queue_item is None:
        return {"error": "Review item not found", "status_code": 404}

    if queue_item.status != "pending":
        return {
            "error": f"Item already {queue_item.status}, cannot reject",
            "status_code": 409,
        }

    queue_item.status = "rejected"
    queue_item.reviewer_notes = reason
    queue_item.reviewed_at = datetime.now(timezone.utc)

    # Deprecate entity or delete relation
    if queue_item.item_type == "entity":
        node = await session.get(KGNode, queue_item.item_id)
        if node is not None:
            node.status = "deprecated"
            logger.info(
                "Rejected entity %s (%s) — status set to deprecated",
                queue_item.item_id,
                node.label,
            )
    elif queue_item.item_type == "relation":
        edge = await session.get(KGEdge, queue_item.item_id)
        if edge is not None:
            await session.delete(edge)
            logger.info(
                "Rejected relation %s — edge deleted",
                queue_item.item_id,
            )

    await session.flush()
    await session.refresh(queue_item)

    return {
        "id": queue_item.id,
        "item_type": queue_item.item_type,
        "item_id": queue_item.item_id,
        "status": queue_item.status,
        "reviewer_notes": queue_item.reviewer_notes,
        "reviewed_at": (
            queue_item.reviewed_at.isoformat() if queue_item.reviewed_at else None
        ),
    }


# ---------------------------------------------------------------------------
# Auto-route helper for graph builders
# ---------------------------------------------------------------------------


async def auto_route_to_review(
    session: AsyncSession,
    *,
    node: KGNode | None = None,
    edge: KGEdge | None = None,
    reason_override: str | None = None,
) -> KGReviewQueue | None:
    """Check if a node or edge should be routed to review and add if so.

    Returns the created queue item, or None if the item has sufficient
    confidence and does not need review.
    """
    if node is not None and should_route_to_review(node.confidence):
        reason = reason_override or (
            f"Low confidence ({node.confidence:.2f}) for "
            f"{node.node_type}: {node.label}"
        )
        node.status = "pending_review"
        await session.flush()
        return await add_to_review_queue(
            session,
            item_type="entity",
            item_id=node.id,
            review_reason=reason,
        )

    if edge is not None and should_route_to_review(edge.confidence):
        reason = reason_override or (
            f"Low confidence ({edge.confidence:.2f}) for "
            f"relation: {edge.relation_type}"
        )
        return await add_to_review_queue(
            session,
            item_type="relation",
            item_id=edge.id,
            review_reason=reason,
        )

    return None
