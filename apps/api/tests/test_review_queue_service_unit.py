"""Unit tests for nfm_db.services.review_queue_service (NFM-1314).

All dependencies are mocked -- no database, no conftest.py fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from nfm_db.services.review_queue_service import (
    add_to_review_queue,
    approve_review_item,
    auto_route_to_review,
    list_pending_reviews,
    reject_review_item,
    should_route_to_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UNSET = object()


def _make_queue_item(
    *,
    item_id: uuid4 | None = None,
    item_type: str = "entity",
    status: str = "pending",
    reviewer_notes: str | None = None,
    created_at: datetime | None = _UNSET,  # type: ignore[assignment]
    reviewed_at: datetime | None = _UNSET,  # type: ignore[assignment]
) -> MagicMock:
    """Build a mock KGReviewQueue row."""
    item = MagicMock()
    item.id = item_id or uuid4()
    item.item_type = item_type
    item.item_id = item_id or uuid4()
    item.review_reason = "Low confidence 0.42 for Material: SiC"
    item.status = status
    item.reviewer_notes = reviewer_notes
    item.created_at = (
        created_at
        if created_at is not _UNSET
        else datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    )
    item.reviewed_at = reviewed_at if reviewed_at is not _UNSET else None
    return item


def _make_node(
    *,
    node_id: uuid4 | None = None,
    node_type: str = "Material",
    label: str = "Silicon Carbide",
    confidence: float = 0.42,
    status: str = "pending_review",
) -> MagicMock:
    mock = MagicMock()
    mock.id = node_id or uuid4()
    mock.node_type = node_type
    mock.label = label
    mock.confidence = confidence
    mock.status = status
    return mock


def _make_edge(
    *,
    edge_id: uuid4 | None = None,
    source_node_id: uuid4 | None = None,
    target_node_id: uuid4 | None = None,
    relation_type: str = "has_property",
    confidence: float = 0.35,
) -> MagicMock:
    mock = MagicMock()
    mock.id = edge_id or uuid4()
    mock.source_node_id = source_node_id or uuid4()
    mock.target_node_id = target_node_id or uuid4()
    mock.relation_type = relation_type
    mock.confidence = confidence
    return mock


# ===================================================================
# should_route_to_review (pure function, no mocks needed)
# ===================================================================


class TestShouldRouteToReview:
    def test_below_threshold_returns_true(self) -> None:
        assert should_route_to_review(0.59) is True

    def test_at_threshold_returns_false(self) -> None:
        assert should_route_to_review(0.6) is False

    def test_above_threshold_returns_false(self) -> None:
        assert should_route_to_review(0.95) is False

    def test_zero_confidence_returns_true(self) -> None:
        assert should_route_to_review(0.0) is True

    def test_negative_confidence_returns_true(self) -> None:
        assert should_route_to_review(-0.1) is True

    def test_just_below_threshold(self) -> None:
        assert should_route_to_review(0.599999) is True


# ===================================================================
# add_to_review_queue
# ===================================================================


class TestAddToReviewQueue:
    @pytest.mark.asyncio
    async def test_creates_queue_item_and_returns_it(self) -> None:
        session = AsyncMock()
        item_id = uuid4()

        # Make refresh set the id on the object
        async def refresh_side_effect(obj: object) -> None:
            cast = MagicMock()
            cast.__setattr__("id", item_id)
            # the object passed is a real-ish KGReviewQueue mock
            obj.id = item_id

        session.refresh = AsyncMock(side_effect=refresh_side_effect)

        with patch(
            "nfm_db.services.review_queue_service.KGReviewQueue",
            autospec=True,
        ) as MockQueueCls:
            mock_instance = MagicMock()
            mock_instance.id = item_id
            MockQueueCls.return_value = mock_instance

            result = await add_to_review_queue(
                session,
                item_type="entity",
                item_id=item_id,
                review_reason="Low confidence",
            )

        MockQueueCls.assert_called_once()
        call_kwargs = MockQueueCls.call_args[1]
        assert call_kwargs["item_type"] == "entity"
        assert call_kwargs["item_id"] == item_id
        assert call_kwargs["review_reason"] == "Low confidence"
        assert call_kwargs["status"] == "pending"

        session.add.assert_called_once_with(mock_instance)
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(mock_instance)
        assert result is mock_instance


# ===================================================================
# list_pending_reviews
# ===================================================================


class TestListPendingReviews:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pending(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        session.execute = AsyncMock(return_value=mock_result)

        items, total = await list_pending_reviews(session)
        assert items == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_entity_items_with_node_data(self) -> None:
        session = AsyncMock()
        item_id = uuid4()
        node_id = uuid4()

        queue_item = _make_queue_item(item_id=item_id, item_type="entity")

        # First execute: count query
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        # Second execute: paginated query
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )

        node = _make_node(node_id=node_id, label="SiC")
        session.get = AsyncMock(return_value=node)

        items, total = await list_pending_reviews(session, item_type="entity")
        assert total == 1
        assert len(items) == 1
        assert items[0]["item_type"] == "entity"
        assert items[0]["entity_data"]["label"] == "SiC"
        assert items[0]["entity_data"]["node_type"] == "Material"
        assert items[0]["entity_data"]["confidence"] == 0.42
        assert items[0]["entity_data"]["status"] == "pending_review"
        session.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_relation_items_with_edge_data(self) -> None:
        session = AsyncMock()
        item_id = uuid4()
        edge = _make_edge(edge_id=item_id)

        queue_item = _make_queue_item(item_id=item_id, item_type="relation")

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )

        session.get = AsyncMock(return_value=edge)

        items, total = await list_pending_reviews(session)
        assert total == 1
        assert items[0]["item_type"] == "relation"
        assert items[0]["relation_data"]["relation_type"] == "has_property"
        assert items[0]["relation_data"]["confidence"] == 0.35
        assert "source_node_id" in items[0]["relation_data"]

    @pytest.mark.asyncio
    async def test_entity_item_with_no_matching_node(self) -> None:
        session = AsyncMock()
        item_id = uuid4()
        queue_item = _make_queue_item(item_id=item_id, item_type="entity")

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )
        session.get = AsyncMock(return_value=None)

        items, total = await list_pending_reviews(session)
        assert total == 1
        assert "entity_data" not in items[0]

    @pytest.mark.asyncio
    async def test_relation_item_with_no_matching_edge(self) -> None:
        session = AsyncMock()
        item_id = uuid4()
        queue_item = _make_queue_item(item_id=item_id, item_type="relation")

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )
        session.get = AsyncMock(return_value=None)

        items, total = await list_pending_reviews(session)
        assert total == 1
        assert "relation_data" not in items[0]

    @pytest.mark.asyncio
    async def test_respects_limit_and_offset(self) -> None:
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 100
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = []

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )

        items, total = await list_pending_reviews(
            session, limit=10, offset=20
        )
        assert total == 100
        assert items == []
        # Two execute calls: count + paginated
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_count_returns_none_treated_as_zero(self) -> None:
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = None
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = []

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )

        items, total = await list_pending_reviews(session)
        assert total == 0

    @pytest.mark.asyncio
    async def test_isoformat_called_on_timestamps(self) -> None:
        session = AsyncMock()
        reviewed_at = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        queue_item = _make_queue_item(
            item_type="entity",
            reviewed_at=reviewed_at,
        )

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )
        session.get = AsyncMock(return_value=None)

        items, total = await list_pending_reviews(session)
        assert items[0]["created_at"] == queue_item.created_at.isoformat()
        assert items[0]["reviewed_at"] == reviewed_at.isoformat()

    @pytest.mark.asyncio
    async def test_none_timestamps_return_none(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(
            item_type="entity",
            created_at=None,
            reviewed_at=None,
        )

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [queue_item]

        session.execute = AsyncMock(
            side_effect=[count_result, page_result]
        )
        session.get = AsyncMock(return_value=None)

        items, total = await list_pending_reviews(session)
        assert items[0]["created_at"] is None
        assert items[0]["reviewed_at"] is None


# ===================================================================
# approve_review_item
# ===================================================================


class TestApproveReviewItem:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        result = await approve_review_item(
            session, review_id=uuid4()
        )
        assert result == {"error": "Review item not found", "status_code": 404}

    @pytest.mark.asyncio
    async def test_returns_409_when_already_approved(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(status="approved")
        session.get = AsyncMock(return_value=queue_item)

        result = await approve_review_item(
            session, review_id=uuid4()
        )
        assert result["status_code"] == 409
        assert "already approved" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_409_when_already_rejected(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(status="rejected")
        session.get = AsyncMock(return_value=queue_item)

        result = await approve_review_item(
            session, review_id=uuid4()
        )
        assert result["status_code"] == 409
        assert "already rejected" in result["error"]

    @pytest.mark.asyncio
    async def test_approves_entity_and_sets_node_active(self) -> None:
        session = AsyncMock()
        node_id = uuid4()
        queue_item = _make_queue_item(
            item_id=node_id,
            item_type="entity",
            status="pending",
        )
        node = _make_node(node_id=node_id, status="pending_review")
        session.get = AsyncMock(side_effect=[queue_item, node])

        result = await approve_review_item(
            session,
            review_id=queue_item.id,
            reviewer_notes="Looks good",
        )

        assert queue_item.status == "approved"
        assert queue_item.reviewer_notes == "Looks good"
        assert queue_item.reviewed_at is not None
        assert node.status == "active"
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(queue_item)
        assert result["status"] == "approved"
        assert result["reviewer_notes"] == "Looks good"

    @pytest.mark.asyncio
    async def test_approves_entity_with_missing_node(self) -> None:
        session = AsyncMock()
        node_id = uuid4()
        queue_item = _make_queue_item(
            item_id=node_id,
            item_type="entity",
            status="pending",
        )
        session.get = AsyncMock(side_effect=[queue_item, None])

        result = await approve_review_item(
            session, review_id=queue_item.id
        )
        assert result["status"] == "approved"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approves_relation_without_node_lookup(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(
            item_type="relation",
            status="pending",
        )
        session.get = AsyncMock(return_value=queue_item)

        result = await approve_review_item(
            session, review_id=queue_item.id
        )
        assert result["status"] == "approved"
        # Only one get call (the queue item), no node get
        assert session.get.await_count == 1

    @pytest.mark.asyncio
    async def test_approve_without_reviewer_notes(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(item_type="relation", status="pending")
        session.get = AsyncMock(return_value=queue_item)

        result = await approve_review_item(
            session, review_id=queue_item.id, reviewer_notes=None
        )
        assert result["status"] == "approved"
        assert result["reviewer_notes"] is None

    @pytest.mark.asyncio
    async def test_returned_dict_contains_reviewed_at_iso(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(item_type="relation", status="pending")
        session.get = AsyncMock(return_value=queue_item)

        result = await approve_review_item(
            session, review_id=queue_item.id
        )
        assert "reviewed_at" in result
        assert result["reviewed_at"] is not None


# ===================================================================
# reject_review_item
# ===================================================================


class TestRejectReviewItem:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        result = await reject_review_item(
            session, review_id=uuid4(), reason="nope"
        )
        assert result == {"error": "Review item not found", "status_code": 404}

    @pytest.mark.asyncio
    async def test_returns_409_when_already_rejected(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(status="rejected")
        session.get = AsyncMock(return_value=queue_item)

        result = await reject_review_item(
            session, review_id=uuid4(), reason="dup"
        )
        assert result["status_code"] == 409
        assert "already rejected" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_409_when_already_approved(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(status="approved")
        session.get = AsyncMock(return_value=queue_item)

        result = await reject_review_item(
            session, review_id=uuid4(), reason="mistake"
        )
        assert result["status_code"] == 409
        assert "already approved" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_entity_and_deprecates_node(self) -> None:
        session = AsyncMock()
        node_id = uuid4()
        queue_item = _make_queue_item(
            item_id=node_id,
            item_type="entity",
            status="pending",
        )
        node = _make_node(node_id=node_id)
        session.get = AsyncMock(side_effect=[queue_item, node])

        result = await reject_review_item(
            session, review_id=queue_item.id, reason="Wrong entity"
        )

        assert queue_item.status == "rejected"
        assert queue_item.reviewer_notes == "Wrong entity"
        assert queue_item.reviewed_at is not None
        assert node.status == "deprecated"
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(queue_item)
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_rejects_entity_with_missing_node(self) -> None:
        session = AsyncMock()
        node_id = uuid4()
        queue_item = _make_queue_item(
            item_id=node_id,
            item_type="entity",
            status="pending",
        )
        session.get = AsyncMock(side_effect=[queue_item, None])

        result = await reject_review_item(
            session, review_id=queue_item.id, reason="Gone"
        )
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_rejects_relation_and_deletes_edge(self) -> None:
        session = AsyncMock()
        edge_id = uuid4()
        queue_item = _make_queue_item(
            item_id=edge_id,
            item_type="relation",
            status="pending",
        )
        edge = _make_edge(edge_id=edge_id)
        session.get = AsyncMock(side_effect=[queue_item, edge])

        result = await reject_review_item(
            session, review_id=queue_item.id, reason="Bad relation"
        )

        assert queue_item.status == "rejected"
        assert queue_item.reviewer_notes == "Bad relation"
        session.delete.assert_called_once_with(edge)
        session.flush.assert_awaited_once()
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_rejects_relation_with_missing_edge(self) -> None:
        session = AsyncMock()
        edge_id = uuid4()
        queue_item = _make_queue_item(
            item_id=edge_id,
            item_type="relation",
            status="pending",
        )
        session.get = AsyncMock(side_effect=[queue_item, None])

        result = await reject_review_item(
            session, review_id=queue_item.id, reason="Gone edge"
        )
        assert result["status"] == "rejected"
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_returned_dict_contains_expected_fields(self) -> None:
        session = AsyncMock()
        queue_item = _make_queue_item(item_type="relation", status="pending")
        session.get = AsyncMock(return_value=queue_item)

        result = await reject_review_item(
            session, review_id=queue_item.id, reason="test"
        )
        assert "id" in result
        assert "item_type" in result
        assert "item_id" in result
        assert "status" in result
        assert "reviewer_notes" in result
        assert "reviewed_at" in result


# ===================================================================
# auto_route_to_review
# ===================================================================


class TestAutoRouteToReview:
    @pytest.mark.asyncio
    async def test_routes_low_confidence_node(self) -> None:
        session = AsyncMock()
        node = _make_node(confidence=0.3)
        expected_queue_item = _make_queue_item()

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
            return_value=expected_queue_item,
        ) as mock_add:
            result = await auto_route_to_review(session, node=node)

        assert result is expected_queue_item
        mock_add.assert_awaited_once()
        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["item_type"] == "entity"
        assert call_kwargs["item_id"] == node.id
        assert "0.30" in call_kwargs["review_reason"]
        assert node.status == "pending_review"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_high_confidence_node(self) -> None:
        session = AsyncMock()
        node = _make_node(confidence=0.95)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await auto_route_to_review(session, node=node)

        assert result is None
        mock_add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_routes_low_confidence_edge(self) -> None:
        session = AsyncMock()
        edge = _make_edge(confidence=0.2)
        expected_queue_item = _make_queue_item()

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
            return_value=expected_queue_item,
        ) as mock_add:
            result = await auto_route_to_review(session, edge=edge)

        assert result is expected_queue_item
        mock_add.assert_awaited_once()
        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["item_type"] == "relation"
        assert call_kwargs["item_id"] == edge.id

    @pytest.mark.asyncio
    async def test_skips_high_confidence_edge(self) -> None:
        session = AsyncMock()
        edge = _make_edge(confidence=0.99)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await auto_route_to_review(session, edge=edge)

        assert result is None
        mock_add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_reason_override_for_node(self) -> None:
        session = AsyncMock()
        node = _make_node(confidence=0.1)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
            return_value=_make_queue_item(),
        ) as mock_add:
            await auto_route_to_review(
                session, node=node, reason_override="Custom reason"
            )

        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["review_reason"] == "Custom reason"

    @pytest.mark.asyncio
    async def test_uses_reason_override_for_edge(self) -> None:
        session = AsyncMock()
        edge = _make_edge(confidence=0.1)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
            return_value=_make_queue_item(),
        ) as mock_add:
            await auto_route_to_review(
                session, edge=edge, reason_override="Edge custom"
            )

        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["review_reason"] == "Edge custom"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_node_or_edge(self) -> None:
        session = AsyncMock()

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await auto_route_to_review(session)

        assert result is None
        mock_add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_node_at_exact_threshold_not_routed(self) -> None:
        session = AsyncMock()
        node = _make_node(confidence=0.6)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await auto_route_to_review(session, node=node)

        assert result is None
        mock_add.assert_not_awaited()
        # Node status should NOT be changed
        assert node.status == "pending_review"  # unchanged from helper default

    @pytest.mark.asyncio
    async def test_edge_at_exact_threshold_not_routed(self) -> None:
        session = AsyncMock()
        edge = _make_edge(confidence=0.6)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await auto_route_to_review(session, edge=edge)

        assert result is None
        mock_add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_node_takes_priority_over_edge(self) -> None:
        """When both node and edge are provided and node is low-confidence,
        only the node should be routed (early return from the first if-block)."""
        session = AsyncMock()
        node = _make_node(confidence=0.1)
        edge = _make_edge(confidence=0.1)

        with patch(
            "nfm_db.services.review_queue_service.add_to_review_queue",
            new_callable=AsyncMock,
            return_value=_make_queue_item(),
        ) as mock_add:
            result = await auto_route_to_review(session, node=node, edge=edge)

        assert result is not None
        # Only one call -- the node path returns early
        assert mock_add.await_count == 1
        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["item_type"] == "entity"