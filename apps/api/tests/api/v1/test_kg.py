"""Integration tests for Knowledge Graph API endpoints.

Tests all 9 KG endpoints across three domains:
  Query (NFM-858):
    - GET  /api/v1/kg/query/property  — find nodes by property value
    - GET  /api/v1/kg/query/relation  — find edges by relation type
    - GET  /api/v1/kg/query/path      — find paths between entities

  Review Queue (NFM-859):
    - GET  /api/v1/kg/review/queue          — list pending review items
    - POST /api/v1/kg/review/{id}/approve   — approve and add to KG
    - POST /api/v1/kg/review/{id}/reject    — reject with reason

  Conflict Resolution (NFM-861):
    - GET  /api/v1/kg/conflicts              — list conflict records
    - POST /api/v1/kg/conflicts/{id}/resolve — resolve a conflict
    - POST /api/v1/kg/fusion                  — run multi-source fusion pipeline

All service-layer calls are mocked to isolate endpoint logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

NODE_ID_A = uuid.uuid4()
NODE_ID_B = uuid.uuid4()
REVIEW_ID = uuid.uuid4()
CONFLICT_ID = uuid.uuid4()
MATERIAL_ID = uuid.uuid4()
PROPERTY_TYPE_ID = uuid.uuid4()


# ===========================================================================
# Property Query — GET /api/v1/kg/query/property
# ===========================================================================


@pytest.mark.asyncio
async def test_property_query_no_filters(async_client: AsyncClient) -> None:
    """GET /kg/query/property with no filters returns empty list."""
    with patch(
        "nfm_db.api.v1.kg.property_query",
        new_callable=AsyncMock,
    ) as mock_prop:
        mock_prop.return_value = {"nodes": [], "total": 0}
        response = await async_client.get("/api/v1/kg/query/property")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0


@pytest.mark.asyncio
async def test_property_query_with_node_type(async_client: AsyncClient) -> None:
    """GET /kg/query/property with node_type filter returns matching nodes."""
    fake_node = {
        "id": str(NODE_ID_A),
        "node_type": "Material",
        "label": "UO2",
        "aliases": [],
        "properties": {},
        "confidence": 0.95,
    }
    with patch(
        "nfm_db.api.v1.kg.property_query",
        new_callable=AsyncMock,
    ) as mock_prop:
        mock_prop.return_value = {"nodes": [fake_node], "total": 1}
        response = await async_client.get(
            "/api/v1/kg/query/property",
            params={"node_type": "Material"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["data"]["nodes"]) == 1
    assert body["data"]["nodes"][0]["node_type"] == "Material"


@pytest.mark.asyncio
async def test_property_query_with_label_and_fuzzy(
    async_client: AsyncClient,
) -> None:
    """GET /kg/query/property with label and fuzzy=true invokes service correctly."""
    with patch(
        "nfm_db.api.v1.kg.property_query",
        new_callable=AsyncMock,
    ) as mock_prop:
        mock_prop.return_value = {"nodes": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/property",
            params={"label": "uranium", "fuzzy": "true"},
        )

    assert response.status_code == 200
    mock_prop.assert_called_once()
    call_kwargs = mock_prop.call_args[1]
    assert call_kwargs["label"] == "uranium"
    assert call_kwargs["fuzzy"] is True


@pytest.mark.asyncio
async def test_property_query_with_property_key_value(
    async_client: AsyncClient,
) -> None:
    """GET /kg/query/property with property_key and property_value."""
    with patch(
        "nfm_db.api.v1.kg.property_query",
        new_callable=AsyncMock,
    ) as mock_prop:
        mock_prop.return_value = {"nodes": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/property",
            params={
                "property_key": "density",
                "property_value": "10.5",
            },
        )

    assert response.status_code == 200
    call_kwargs = mock_prop.call_args[1]
    assert call_kwargs["property_key"] == "density"
    assert call_kwargs["property_value"] == "10.5"


@pytest.mark.asyncio
async def test_property_query_pagination(async_client: AsyncClient) -> None:
    """GET /kg/query/property with limit and offset parameters."""
    with patch(
        "nfm_db.api.v1.kg.property_query",
        new_callable=AsyncMock,
    ) as mock_prop:
        mock_prop.return_value = {"nodes": [], "total": 5}
        response = await async_client.get(
            "/api/v1/kg/query/property",
            params={"limit": 5, "offset": 10},
        )

    assert response.status_code == 200
    call_kwargs = mock_prop.call_args[1]
    assert call_kwargs["limit"] == 5
    assert call_kwargs["offset"] == 10


# ===========================================================================
# Relation Query — GET /api/v1/kg/query/relation
# ===========================================================================


@pytest.mark.asyncio
async def test_relation_query_no_filters(async_client: AsyncClient) -> None:
    """GET /kg/query/relation with no filters returns empty result."""
    with patch(
        "nfm_db.api.v1.kg.relation_query",
        new_callable=AsyncMock,
    ) as mock_rel:
        mock_rel.return_value = {"edges": [], "nodes": [], "total": 0}
        response = await async_client.get("/api/v1/kg/query/relation")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0


@pytest.mark.asyncio
async def test_relation_query_with_source_and_target(
    async_client: AsyncClient,
) -> None:
    """GET /kg/query/relation with source_node_id and target_node_id."""
    with patch(
        "nfm_db.api.v1.kg.relation_query",
        new_callable=AsyncMock,
    ) as mock_rel:
        mock_rel.return_value = {
            "edges": [],
            "nodes": [],
            "total": 0,
        }
        response = await async_client.get(
            "/api/v1/kg/query/relation",
            params={
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
            },
        )

    assert response.status_code == 200
    mock_rel.assert_called_once()
    call_kwargs = mock_rel.call_args[1]
    assert call_kwargs["source_node_id"] == NODE_ID_A
    assert call_kwargs["target_node_id"] == NODE_ID_B


@pytest.mark.asyncio
async def test_relation_query_with_relation_type(
    async_client: AsyncClient,
) -> None:
    """GET /kg/query/relation with relation_type filter."""
    with patch(
        "nfm_db.api.v1.kg.relation_query",
        new_callable=AsyncMock,
    ) as mock_rel:
        mock_rel.return_value = {"edges": [], "nodes": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"relation_type": "hasProperty"},
        )

    assert response.status_code == 200
    call_kwargs = mock_rel.call_args[1]
    assert call_kwargs["relation_type"] == "hasProperty"


@pytest.mark.asyncio
async def test_relation_query_direction_outgoing(
    async_client: AsyncClient,
) -> None:
    """GET /kg/query/relation with direction=outgoing."""
    with patch(
        "nfm_db.api.v1.kg.relation_query",
        new_callable=AsyncMock,
    ) as mock_rel:
        mock_rel.return_value = {"edges": [], "nodes": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"source_node_id": str(NODE_ID_A), "direction": "outgoing"},
        )

    assert response.status_code == 200
    call_kwargs = mock_rel.call_args[1]
    assert call_kwargs["direction"] == "outgoing"


@pytest.mark.asyncio
async def test_relation_query_invalid_source_uuid(async_client: AsyncClient) -> None:
    """GET /kg/query/relation with invalid source_node_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/query/relation",
        params={"source_node_id": "not-a-uuid"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_relation_query_invalid_target_uuid(async_client: AsyncClient) -> None:
    """GET /kg/query/relation with invalid target_node_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/query/relation",
        params={"target_node_id": "not-a-uuid"},
    )

    assert response.status_code == 400


# ===========================================================================
# Path Query — GET /api/v1/kg/query/path
# ===========================================================================


@pytest.mark.asyncio
async def test_path_query_happy_path(async_client: AsyncClient) -> None:
    """GET /kg/query/path finds paths between two nodes."""
    with patch(
        "nfm_db.api.v1.kg.path_query",
        new_callable=AsyncMock,
    ) as mock_path:
        mock_path.return_value = {"paths": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    mock_path.assert_called_once()
    call_kwargs = mock_path.call_args[1]
    assert call_kwargs["source_node_id"] == NODE_ID_A
    assert call_kwargs["target_node_id"] == NODE_ID_B


@pytest.mark.asyncio
async def test_path_query_with_max_depth(async_client: AsyncClient) -> None:
    """GET /kg/query/path with custom max_depth."""
    with patch(
        "nfm_db.api.v1.kg.path_query",
        new_callable=AsyncMock,
    ) as mock_path:
        mock_path.return_value = {"paths": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
                "max_depth": 5,
            },
        )

    assert response.status_code == 200
    call_kwargs = mock_path.call_args[1]
    assert call_kwargs["max_depth"] == 5


@pytest.mark.asyncio
async def test_path_query_with_relation_types(async_client: AsyncClient) -> None:
    """GET /kg/query/path with comma-separated relation_types filter."""
    with patch(
        "nfm_db.api.v1.kg.path_query",
        new_callable=AsyncMock,
    ) as mock_path:
        mock_path.return_value = {"paths": [], "total": 0}
        response = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
                "relation_types": "hasProperty,relatedTo",
            },
        )

    assert response.status_code == 200
    call_kwargs = mock_path.call_args[1]
    assert call_kwargs["relation_types"] == ["hasProperty", "relatedTo"]


@pytest.mark.asyncio
async def test_path_query_invalid_source_uuid(async_client: AsyncClient) -> None:
    """GET /kg/query/path with invalid source_node_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/query/path",
        params={
            "source_node_id": "bad-uuid",
            "target_node_id": str(NODE_ID_B),
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_path_query_invalid_target_uuid(async_client: AsyncClient) -> None:
    """GET /kg/query/path with invalid target_node_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/query/path",
        params={
            "source_node_id": str(NODE_ID_A),
            "target_node_id": "bad-uuid",
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_path_query_returns_results(async_client: AsyncClient) -> None:
    """GET /kg/query/path returns path results with correct shape."""
    fake_path = {
        "nodes": [
            {
                "id": str(NODE_ID_A),
                "node_type": "Material",
                "label": "UO2",
                "aliases": [],
                "properties": {},
                "confidence": 0.95,
            },
            {
                "id": str(NODE_ID_B),
                "node_type": "Property",
                "label": "density",
                "aliases": [],
                "properties": {},
                "confidence": 0.9,
            },
        ],
        "edges": [
            {
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
                "relation_type": "hasProperty",
            },
        ],
        "length": 1,
    }
    with patch(
        "nfm_db.api.v1.kg.path_query",
        new_callable=AsyncMock,
    ) as mock_path:
        mock_path.return_value = {"paths": [fake_path], "total": 1}
        response = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(NODE_ID_A),
                "target_node_id": str(NODE_ID_B),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 1
    assert len(body["data"]["paths"]) == 1
    assert body["data"]["paths"][0]["length"] == 1


# ===========================================================================
# Review Queue — GET /api/v1/kg/review/queue
# ===========================================================================


@pytest.mark.asyncio
async def test_list_review_queue_empty(async_client: AsyncClient) -> None:
    """GET /kg/review/queue with no pending items returns empty list."""
    with patch(
        "nfm_db.api.v1.kg.list_pending_reviews",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 0)
        response = await async_client.get("/api/v1/kg/review/queue")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_review_queue_with_items(async_client: AsyncClient) -> None:
    """GET /kg/review/queue returns pending items."""
    fake_items = [
        {
            "id": str(REVIEW_ID),
            "item_type": "entity",
            "item_id": str(NODE_ID_A),
            "review_reason": "Low confidence",
            "status": "pending",
            "reviewer_notes": None,
            "created_at": "2025-01-01T00:00:00+00:00",
            "reviewed_at": None,
        },
    ]
    with patch(
        "nfm_db.api.v1.kg.list_pending_reviews",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = (fake_items, 1)
        response = await async_client.get("/api/v1/kg/review/queue")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["item_type"] == "entity"


@pytest.mark.asyncio
async def test_list_review_queue_filter_by_type(
    async_client: AsyncClient,
) -> None:
    """GET /kg/review/queue with item_type filter."""
    with patch(
        "nfm_db.api.v1.kg.list_pending_reviews",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 0)
        response = await async_client.get(
            "/api/v1/kg/review/queue",
            params={"item_type": "relation"},
        )

    assert response.status_code == 200
    mock_list.assert_called_once()
    call_kwargs = mock_list.call_args[1]
    assert call_kwargs["item_type"] == "relation"


@pytest.mark.asyncio
async def test_list_review_queue_pagination(async_client: AsyncClient) -> None:
    """GET /kg/review/queue with limit and offset."""
    with patch(
        "nfm_db.api.v1.kg.list_pending_reviews",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 50)
        response = await async_client.get(
            "/api/v1/kg/review/queue",
            params={"limit": 10, "offset": 20},
        )

    assert response.status_code == 200
    call_kwargs = mock_list.call_args[1]
    assert call_kwargs["limit"] == 10
    assert call_kwargs["offset"] == 20


# ===========================================================================
# Review Queue — POST /api/v1/kg/review/{id}/approve
# ===========================================================================


@pytest.mark.asyncio
async def test_approve_review_success(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/approve approves a pending item."""
    fake_result = {
        "id": str(REVIEW_ID),
        "item_type": "entity",
        "item_id": str(NODE_ID_A),
        "status": "approved",
        "reviewer_notes": "Looks good",
        "reviewed_at": "2025-01-01T00:00:00+00:00",
    }
    with patch(
        "nfm_db.api.v1.kg.approve_review_item",
        new_callable=AsyncMock,
    ) as mock_approve:
        mock_approve.return_value = fake_result
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/approve",
            json={"reviewer_notes": "Looks good"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_review_no_body(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/approve without a request body."""
    fake_result = {
        "id": str(REVIEW_ID),
        "item_type": "entity",
        "item_id": str(NODE_ID_A),
        "status": "approved",
        "reviewer_notes": None,
        "reviewed_at": "2025-01-01T00:00:00+00:00",
    }
    with patch(
        "nfm_db.api.v1.kg.approve_review_item",
        new_callable=AsyncMock,
    ) as mock_approve:
        mock_approve.return_value = fake_result
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/approve",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True


@pytest.mark.asyncio
async def test_approve_review_not_found(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/approve with non-existent review_id returns 404."""
    with patch(
        "nfm_db.api.v1.kg.approve_review_item",
        new_callable=AsyncMock,
    ) as mock_approve:
        mock_approve.return_value = {
            "error": "Review item not found",
            "status_code": 404,
        }
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/approve",
            json={"reviewer_notes": "OK"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_review_already_approved(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/approve on already-approved item returns 409."""
    with patch(
        "nfm_db.api.v1.kg.approve_review_item",
        new_callable=AsyncMock,
    ) as mock_approve:
        mock_approve.return_value = {
            "error": "Item already approved, cannot approve",
            "status_code": 409,
        }
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/approve",
            json={"reviewer_notes": "OK"},
        )

    assert response.status_code == 409


# ===========================================================================
# Review Queue — POST /api/v1/kg/review/{id}/reject
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_review_success(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/reject rejects a pending item."""
    fake_result = {
        "id": str(REVIEW_ID),
        "item_type": "entity",
        "item_id": str(NODE_ID_A),
        "status": "rejected",
        "reviewer_notes": "Duplicate entry",
        "reviewed_at": "2025-01-01T00:00:00+00:00",
    }
    with patch(
        "nfm_db.api.v1.kg.reject_review_item",
        new_callable=AsyncMock,
    ) as mock_reject:
        mock_reject.return_value = fake_result
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/reject",
            json={"reason": "Duplicate entry"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_reject_review_missing_reason(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/reject without reason returns 422."""
    response = await async_client.post(
        f"/api/v1/kg/review/{REVIEW_ID}/reject",
        json={},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_review_empty_reason(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/reject with empty reason returns 422."""
    response = await async_client.post(
        f"/api/v1/kg/review/{REVIEW_ID}/reject",
        json={"reason": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_review_not_found(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/reject with non-existent review_id returns 404."""
    with patch(
        "nfm_db.api.v1.kg.reject_review_item",
        new_callable=AsyncMock,
    ) as mock_reject:
        mock_reject.return_value = {
            "error": "Review item not found",
            "status_code": 404,
        }
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/reject",
            json={"reason": "Not valid"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reject_review_already_rejected(async_client: AsyncClient) -> None:
    """POST /kg/review/{id}/reject on already-rejected item returns 409."""
    with patch(
        "nfm_db.api.v1.kg.reject_review_item",
        new_callable=AsyncMock,
    ) as mock_reject:
        mock_reject.return_value = {
            "error": "Item already rejected, cannot reject",
            "status_code": 409,
        }
        response = await async_client.post(
            f"/api/v1/kg/review/{REVIEW_ID}/reject",
            json={"reason": "Already rejected"},
        )

    assert response.status_code == 409


# ===========================================================================
# Conflicts — GET /api/v1/kg/conflicts
# ===========================================================================


@pytest.mark.asyncio
async def test_list_conflicts_empty(async_client: AsyncClient) -> None:
    """GET /kg/conflicts with no conflict records returns empty list."""
    with patch(
        "nfm_db.api.v1.kg.list_conflicts",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 0)
        response = await async_client.get("/api/v1/kg/conflicts")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["conflicts"] == []
    assert body["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_conflicts_with_records(async_client: AsyncClient) -> None:
    """GET /kg/conflicts returns conflict records."""
    now = datetime.now(timezone.utc)
    conflict_record = _make_fake_conflict_record(
        created_at=now,
        updated_at=now,
    )
    with patch(
        "nfm_db.api.v1.kg.list_conflicts",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([conflict_record], 1)
        response = await async_client.get("/api/v1/kg/conflicts")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["conflicts"][0]["id"] == str(CONFLICT_ID)


@pytest.mark.asyncio
async def test_list_conflicts_filter_by_material_id(
    async_client: AsyncClient,
) -> None:
    """GET /kg/conflicts with material_id filter."""
    with patch(
        "nfm_db.api.v1.kg.list_conflicts",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 0)
        response = await async_client.get(
            "/api/v1/kg/conflicts",
            params={"material_id": str(MATERIAL_ID)},
        )

    assert response.status_code == 200
    call_kwargs = mock_list.call_args[1]
    assert call_kwargs["material_id"] == MATERIAL_ID


@pytest.mark.asyncio
async def test_list_conflicts_invalid_material_id(
    async_client: AsyncClient,
) -> None:
    """GET /kg/conflicts with invalid material_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/conflicts",
        params={"material_id": "not-a-uuid"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_conflicts_filter_by_status(
    async_client: AsyncClient,
) -> None:
    """GET /kg/conflicts with status filter."""
    with patch(
        "nfm_db.api.v1.kg.list_conflicts",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = ([], 0)
        response = await async_client.get(
            "/api/v1/kg/conflicts",
            params={"status": "pending"},
        )

    assert response.status_code == 200
    call_kwargs = mock_list.call_args[1]
    assert call_kwargs["status"] == "pending"


@pytest.mark.asyncio
async def test_list_conflicts_invalid_property_type_id(
    async_client: AsyncClient,
) -> None:
    """GET /kg/conflicts with invalid property_type_id returns 400."""
    response = await async_client.get(
        "/api/v1/kg/conflicts",
        params={"property_type_id": "bad"},
    )

    assert response.status_code == 400


# ===========================================================================
# Conflicts — POST /api/v1/kg/conflicts/{id}/resolve
# ===========================================================================


@pytest.mark.asyncio
async def test_resolve_conflict_success(async_client: AsyncClient) -> None:
    """POST /kg/conflicts/{id}/resolve resolves a conflict."""
    record = _make_fake_conflict_record(
        status="resolved",
        strategy="confidence",
        resolved_value={"scalar": 10.5, "unit": "W/mK"},
        resolved_at=datetime.now(timezone.utc),
    )
    with patch(
        "nfm_db.api.v1.kg.resolve_single_conflict",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = record
        response = await async_client.post(
            f"/api/v1/kg/conflicts/{CONFLICT_ID}/resolve",
            json={
                "strategy_override": "confidence",
                "resolved_value": {"scalar": 10.5, "unit": "W/mK"},
                "notes": "Manually selected highest confidence value",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "resolved"
    assert body["data"]["strategy"] == "confidence"


@pytest.mark.asyncio
async def test_resolve_conflict_no_body(async_client: AsyncClient) -> None:
    """POST /kg/conflicts/{id}/resolve without a request body (auto-resolve)."""
    record = _make_fake_conflict_record(
        status="resolved",
        strategy="consensus",
        resolved_value={"value": 10.0},
    )
    with patch(
        "nfm_db.api.v1.kg.resolve_single_conflict",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = record
        response = await async_client.post(
            f"/api/v1/kg/conflicts/{CONFLICT_ID}/resolve",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True


@pytest.mark.asyncio
async def test_resolve_conflict_not_found(async_client: AsyncClient) -> None:
    """POST /kg/conflicts/{id}/resolve with non-existent conflict returns 404."""
    with patch(
        "nfm_db.api.v1.kg.resolve_single_conflict",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = None
        response = await async_client.post(
            f"/api/v1/kg/conflicts/{CONFLICT_ID}/resolve",
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resolve_conflict_with_notes_only(
    async_client: AsyncClient,
) -> None:
    """POST /kg/conflicts/{id}/resolve with only notes."""
    record = _make_fake_conflict_record(
        status="escalated",
        strategy="manual",
    )
    with patch(
        "nfm_db.api.v1.kg.resolve_single_conflict",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = record
        response = await async_client.post(
            f"/api/v1/kg/conflicts/{CONFLICT_ID}/resolve",
            json={"notes": "Needs manual review"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "escalated"


# ===========================================================================
# Fusion — POST /api/v1/kg/fusion
# ===========================================================================


@pytest.mark.asyncio
async def test_run_fusion_success(async_client: AsyncClient) -> None:
    """POST /kg/fusion runs fusion pipeline and returns result."""
    from nfm_db.schemas.conflict import FusionResult

    fake_result = FusionResult(
        conflicts_detected=3,
        conflicts_resolved=2,
        conflicts_escalated=1,
        errors=[],
    )
    with patch(
        "nfm_db.api.v1.kg.run_fusion",
        new_callable=AsyncMock,
    ) as mock_fusion:
        mock_fusion.return_value = fake_result
        response = await async_client.post("/api/v1/kg/fusion")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["conflicts_detected"] == 3
    assert body["data"]["conflicts_resolved"] == 2
    assert body["data"]["conflicts_escalated"] == 1


@pytest.mark.asyncio
async def test_run_fusion_with_material_id(async_client: AsyncClient) -> None:
    """POST /kg/fusion with material_id query parameter."""
    from nfm_db.schemas.conflict import FusionResult

    fake_result = FusionResult(
        conflicts_detected=1,
        conflicts_resolved=1,
        conflicts_escalated=0,
        errors=[],
    )
    with patch(
        "nfm_db.api.v1.kg.run_fusion",
        new_callable=AsyncMock,
    ) as mock_fusion:
        mock_fusion.return_value = fake_result
        response = await async_client.post(
            "/api/v1/kg/fusion",
            params={"material_id": str(MATERIAL_ID)},
        )

    assert response.status_code == 200
    mock_fusion.assert_called_once()
    call_kwargs = mock_fusion.call_args[1]
    assert call_kwargs["material_id"] == MATERIAL_ID


@pytest.mark.asyncio
async def test_run_fusion_with_strategy_override(
    async_client: AsyncClient,
) -> None:
    """POST /kg/fusion with strategy_override query parameter."""
    from nfm_db.schemas.conflict import FusionResult

    fake_result = FusionResult(
        conflicts_detected=0,
        conflicts_resolved=0,
        conflicts_escalated=0,
        errors=[],
    )
    with patch(
        "nfm_db.api.v1.kg.run_fusion",
        new_callable=AsyncMock,
    ) as mock_fusion:
        mock_fusion.return_value = fake_result
        response = await async_client.post(
            "/api/v1/kg/fusion",
            params={"strategy_override": "newest"},
        )

    assert response.status_code == 200
    call_kwargs = mock_fusion.call_args[1]
    assert call_kwargs["strategy_override"] == "newest"


@pytest.mark.asyncio
async def test_run_fusion_with_errors(async_client: AsyncClient) -> None:
    """POST /kg/fusion returns errors when detection fails."""
    from nfm_db.schemas.conflict import FusionResult

    fake_result = FusionResult(
        conflicts_detected=0,
        conflicts_resolved=0,
        conflicts_escalated=0,
        errors=["Detection failed: connection error"],
    )
    with patch(
        "nfm_db.api.v1.kg.run_fusion",
        new_callable=AsyncMock,
    ) as mock_fusion:
        mock_fusion.return_value = fake_result
        response = await async_client.post("/api/v1/kg/fusion")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["errors"] == ["Detection failed: connection error"]


@pytest.mark.asyncio
async def test_run_fusion_invalid_material_id(
    async_client: AsyncClient,
) -> None:
    """POST /kg/fusion with invalid material_id returns 400."""
    response = await async_client.post(
        "/api/v1/kg/fusion",
        params={"material_id": "not-a-uuid"},
    )

    assert response.status_code == 400


# ===========================================================================
# Helpers
# ===========================================================================


def _make_fake_conflict_record(
    *,
    status: str = "pending",
    strategy: str = "confidence",
    resolved_value: dict | None = None,
    resolved_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    """Build a MagicMock mimicking a ConflictRecord ORM object.

    The endpoint code accesses attributes (e.g. ``record.id``),
    so a plain dict does not work.
    """
    if created_at is None:
        created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    if updated_at is None:
        updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    record = MagicMock()
    record.id = CONFLICT_ID
    record.material_node_id = MATERIAL_ID
    record.property_node_id = NODE_ID_B
    record.property_type_id = PROPERTY_TYPE_ID
    record.conflicting_values = [
        {"value": {"scalar": 10.0}, "source_id": None},
    ]
    record.strategy = strategy
    record.resolved_value = resolved_value
    record.status = status
    record.resolved_by = None
    record.resolved_at = resolved_at
    record.resolution_notes = None
    record.created_at = created_at
    record.updated_at = updated_at
    return record
