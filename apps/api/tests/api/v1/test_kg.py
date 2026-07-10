"""Integration tests for /api/v1/kg endpoints.

Covers the 9 knowledge graph endpoints (Phase 2):
- GET  /nodes/{node_type}/{node_id}        — Property query
- GET  /nodes/{node_id}/relations          — Relation query
- POST /path                              — Path query (recursive CTE)
- POST /ingest                             — Incremental update (202)
- GET  /ingest/{batch_id}                  — Poll ingest status
- GET  /review                             — List review queue
- POST /review/{item_id}/approve           — Approve review item
- POST /review/{item_id}/reject            — Reject review item
- GET  /search                             — Fuzzy label search
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models.kg import KGEdge, KGNode, KGReviewQueue

# ---------------------------------------------------------------------------
# Helpers — each test creates its own data, no cross-test dependencies
# ---------------------------------------------------------------------------


async def _seed_node(
    db_session,
    node_type: str = "Material",
    label: str = "UO2",
    **overrides,
) -> KGNode:
    defaults = dict(
        node_type=node_type,
        label=label,
        aliases=["Uranium Dioxide"],
        properties={"density": 10.97},
        confidence=0.95,
        status="active",
    )
    defaults.update(overrides)
    node = KGNode(**defaults)
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def _seed_edge(
    db_session,
    source: KGNode,
    target: KGNode,
    relation_type: str = "contains",
    **overrides,
) -> KGEdge:
    defaults = dict(
        source_node_id=source.id,
        target_node_id=target.id,
        relation_type=relation_type,
        properties={"weight": 1.0},
        confidence=0.9,
    )
    defaults.update(overrides)
    edge = KGEdge(**defaults)
    db_session.add(edge)
    await db_session.commit()
    await db_session.refresh(edge)
    return edge


async def _seed_review_item(
    db_session,
    item_type: str = "node",
    status: str = "pending",
    **overrides,
) -> KGReviewQueue:
    defaults = dict(
        item_type=item_type,
        item_id=uuid.uuid4(),
        review_reason="Duplicate label detected",
        status=status,
    )
    defaults.update(overrides)
    item = KGReviewQueue(**defaults)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# N1: GET /api/v1/kg/nodes/{node_type}/{node_id} — Property query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_node_success(async_client, db_session) -> None:
    """GET /kg/nodes/Material/{id} returns the seeded node."""
    node = await _seed_node(db_session)

    response = await async_client.get(
        f"/api/v1/kg/nodes/Material/{node.id}",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["id"] == str(node.id)
    assert data["node_type"] == "Material"
    assert data["label"] == "UO2"
    assert data["aliases"] == ["Uranium Dioxide"]
    assert data["confidence"] == 0.95
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_get_node_invalid_type(async_client, db_session) -> None:
    """GET /kg/nodes/InvalidType/{id} returns 400."""
    node = await _seed_node(db_session)

    response = await async_client.get(
        f"/api/v1/kg/nodes/InvalidType/{node.id}",
    )
    assert response.status_code == 400
    assert "Invalid node_type" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_node_type_mismatch(async_client, db_session) -> None:
    """GET /kg/nodes/Element/{material_id} returns 404 (type mismatch)."""
    node = await _seed_node(db_session, node_type="Material")

    response = await async_client.get(
        f"/api/v1/kg/nodes/Element/{node.id}",
    )
    assert response.status_code == 404
    assert "Node not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_node_not_found(async_client) -> None:
    """GET /kg/nodes/Material/{nonexistent} returns 404."""
    fake_id = uuid.uuid4()
    response = await async_client.get(
        f"/api/v1/kg/nodes/Material/{fake_id}",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# N2: GET /api/v1/kg/nodes/{node_id}/relations — Relation query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_relations_outgoing(async_client, db_session) -> None:
    """GET /kg/nodes/{id}/relations returns outgoing edges."""
    node_a = await _seed_node(db_session, label="NodeA")
    node_b = await _seed_node(db_session, label="NodeB")
    await _seed_edge(db_session, source=node_a, target=node_b, relation_type="contains")

    response = await async_client.get(
        f"/api/v1/kg/nodes/{node_a.id}/relations",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    edge = data["items"][0]
    assert edge["relation_type"] == "contains"
    assert edge["source_node"]["id"] == str(node_a.id)
    assert edge["target_node"]["id"] == str(node_b.id)


@pytest.mark.asyncio
async def test_get_relations_node_not_found(async_client) -> None:
    """GET /kg/nodes/{nonexistent}/relations returns 404."""
    fake_id = uuid.uuid4()
    response = await async_client.get(
        f"/api/v1/kg/nodes/{fake_id}/relations",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_relations_empty(async_client, db_session) -> None:
    """GET /kg/nodes/{id}/relations returns empty for node with no edges."""
    node = await _seed_node(db_session)

    response = await async_client.get(
        f"/api/v1/kg/nodes/{node.id}/relations",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_relations_with_relation_type_filter(
    async_client, db_session,
) -> None:
    """GET /kg/nodes/{id}/relations?relation_type=contains filters correctly."""
    node_a = await _seed_node(db_session, label="NodeA")
    node_b = await _seed_node(db_session, label="NodeB")
    node_c = await _seed_node(db_session, label="NodeC")
    await _seed_edge(db_session, source=node_a, target=node_b, relation_type="contains")
    await _seed_edge(db_session, source=node_a, target=node_c, relation_type="derived_from")

    response = await async_client.get(
        f"/api/v1/kg/nodes/{node_a.id}/relations?relation_type=contains",
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert all(e["relation_type"] == "contains" for e in items)


@pytest.mark.asyncio
async def test_get_relations_pagination(async_client, db_session) -> None:
    """GET /kg/nodes/{id}/relations?limit=1&offset=0 respects pagination."""
    node_a = await _seed_node(db_session, label="NodeA")
    node_b = await _seed_node(db_session, label="NodeB")
    node_c = await _seed_node(db_session, label="NodeC")
    await _seed_edge(db_session, source=node_a, target=node_b)
    await _seed_edge(db_session, source=node_a, target=node_c)

    response = await async_client.get(
        f"/api/v1/kg/nodes/{node_a.id}/relations?limit=1&offset=0",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) <= 1
    assert data["limit"] == 1


# ---------------------------------------------------------------------------
# N3: POST /api/v1/kg/path — Path query (recursive CTE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_paths_source_not_found(async_client) -> None:
    """POST /kg/path with nonexistent source returns 404."""
    fake_target = uuid.uuid4()
    response = await async_client.post(
        "/api/v1/kg/path",
        json={
            "source_id": str(uuid.uuid4()),
            "target_id": str(fake_target),
            "max_depth": 3,
        },
    )
    assert response.status_code == 404
    assert "Source node not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_find_paths_target_not_found(async_client, db_session) -> None:
    """POST /kg/path with nonexistent target returns 404."""
    node = await _seed_node(db_session)
    response = await async_client.post(
        "/api/v1/kg/path",
        json={
            "source_id": str(node.id),
            "target_id": str(uuid.uuid4()),
            "max_depth": 3,
        },
    )
    assert response.status_code == 404
    assert "Target node not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_find_paths_no_path(async_client, db_session) -> None:
    """POST /kg/path with disconnected nodes returns empty list."""
    node_a = await _seed_node(db_session, label="NodeA")
    node_b = await _seed_node(db_session, label="NodeB")

    response = await async_client.post(
        "/api/v1/kg/path",
        json={
            "source_id": str(node_a.id),
            "target_id": str(node_b.id),
            "max_depth": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_find_paths_validation(async_client) -> None:
    """POST /kg/path with max_depth=0 fails validation."""
    response = await async_client.post(
        "/api/v1/kg/path",
        json={
            "source_id": str(uuid.uuid4()),
            "target_id": str(uuid.uuid4()),
            "max_depth": 0,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# N4: POST /api/v1/kg/ingest — Incremental update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_success(async_client) -> None:
    """POST /kg/ingest returns 202 with batch_id and pending status."""
    response = await async_client.post(
        "/api/v1/kg/ingest",
        json={"text": "UO2 fuel pellets have density 10.97 g/cm3"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert "batch_id" in body["data"]
    assert body["data"]["status"] == "pending"


@pytest.mark.asyncio
async def test_ingest_with_source_id(async_client) -> None:
    """POST /kg/ingest with source_id returns 202."""
    response = await async_client.post(
        "/api/v1/kg/ingest",
        json={
            "text": "Some text about materials",
            "source_id": "paper-123",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["data"]["status"] == "pending"


@pytest.mark.asyncio
async def test_ingest_empty_text(async_client) -> None:
    """POST /kg/ingest with empty text fails validation."""
    response = await async_client.post(
        "/api/v1/kg/ingest",
        json={"text": ""},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# N5: GET /api/v1/kg/ingest/{batch_id} — Poll ingest status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_ingest_success(async_client) -> None:
    """GET /kg/ingest/{batch_id} returns status after ingest."""
    ingest_resp = await async_client.post(
        "/api/v1/kg/ingest",
        json={"text": "Test material data"},
    )
    batch_id = ingest_resp.json()["data"]["batch_id"]

    response = await async_client.get(f"/api/v1/kg/ingest/{batch_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["batch_id"] == batch_id
    assert data["status"] in ("pending", "completed", "processing", "failed")


@pytest.mark.asyncio
async def test_poll_ingest_not_found(async_client) -> None:
    """GET /kg/ingest/{nonexistent} returns 404."""
    response = await async_client.get(f"/api/v1/kg/ingest/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# N6: GET /api/v1/kg/review — List review queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_review_empty(async_client) -> None:
    """GET /kg/review returns empty list when no items exist."""
    response = await async_client.get("/api/v1/kg/review")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_review_returns_items(async_client, db_session) -> None:
    """GET /kg/review returns seeded review items."""
    await _seed_review_item(db_session, item_type="node", status="pending")
    await _seed_review_item(db_session, item_type="edge", status="pending")

    response = await async_client.get("/api/v1/kg/review")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_review_filter_by_status(async_client, db_session) -> None:
    """GET /kg/review?status=pending filters correctly."""
    await _seed_review_item(db_session, status="pending")
    await _seed_review_item(db_session, status="approved")

    response = await async_client.get("/api/v1/kg/review?status=pending")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_list_review_pagination(async_client, db_session) -> None:
    """GET /kg/review?limit=1&offset=0 respects pagination."""
    await _seed_review_item(db_session)
    await _seed_review_item(db_session)

    response = await async_client.get("/api/v1/kg/review?limit=1&offset=0")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) <= 1
    assert data["limit"] == 1


# ---------------------------------------------------------------------------
# N7: POST /api/v1/kg/review/{item_id}/approve — Approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_review_item(async_client, db_session) -> None:
    """POST /kg/review/{id}/approve transitions item to approved."""
    item = await _seed_review_item(db_session, status="pending")

    response = await async_client.post(
        f"/api/v1/kg/review/{item.id}/approve",
        json={"reviewer_notes": "Verified correct"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "approved"
    assert data["reviewer_notes"] == "Verified correct"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_approve_review_item_not_found(async_client) -> None:
    """POST /kg/review/{nonexistent}/approve returns 404."""
    response = await async_client.post(
        f"/api/v1/kg/review/{uuid.uuid4()}/approve",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_review_item_no_body(async_client, db_session) -> None:
    """POST /kg/review/{id}/approve without body still works."""
    item = await _seed_review_item(db_session, status="pending")

    response = await async_client.post(
        f"/api/v1/kg/review/{item.id}/approve",
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"


# ---------------------------------------------------------------------------
# N8: POST /api/v1/kg/review/{item_id}/reject — Reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_review_item(async_client, db_session) -> None:
    """POST /kg/review/{id}/reject transitions item to rejected."""
    item = await _seed_review_item(db_session, status="pending")

    response = await async_client.post(
        f"/api/v1/kg/review/{item.id}/reject",
        json={"reviewer_notes": "Incorrect data"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "rejected"
    assert data["reviewer_notes"] == "Incorrect data"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_reject_review_item_not_found(async_client) -> None:
    """POST /kg/review/{nonexistent}/reject returns 404."""
    response = await async_client.post(
        f"/api/v1/kg/review/{uuid.uuid4()}/reject",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# N9: GET /api/v1/kg/search — Fuzzy label search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_nodes_by_label(async_client, db_session) -> None:
    """GET /kg/search?q=UO2 returns matching nodes."""
    await _seed_node(db_session, label="UO2")
    await _seed_node(db_session, label="UO2-Shell")

    response = await async_client.get("/api/v1/kg/search?q=UO2")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert len(data) >= 1
    labels = [n["label"] for n in data]
    assert any("UO2" in label for label in labels)


@pytest.mark.asyncio
async def test_search_nodes_by_type_filter(async_client, db_session) -> None:
    """GET /kg/search?q=UO2&type=Material filters by node_type."""
    await _seed_node(db_session, label="UO2", node_type="Material")

    response = await async_client.get("/api/v1/kg/search?q=UO2&type=Material")
    assert response.status_code == 200
    data = response.json()["data"]
    assert all(n["node_type"] == "Material" for n in data)


@pytest.mark.asyncio
async def test_search_nodes_invalid_type(async_client) -> None:
    """GET /kg/search?q=test&type=InvalidType returns 400."""
    response = await async_client.get("/api/v1/kg/search?q=test&type=InvalidType")
    assert response.status_code == 400
    assert "Invalid type" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_nodes_empty_result(async_client) -> None:
    """GET /kg/search?q=NonexistentLabel returns empty list."""
    response = await async_client.get("/api/v1/kg/search?q=NonexistentLabel")
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_search_nodes_no_query(async_client) -> None:
    """GET /kg/search without q parameter returns 422."""
    response = await async_client.get("/api/v1/kg/search")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_nodes_limit(async_client, db_session) -> None:
    """GET /kg/search?q=U respects limit parameter."""
    await _seed_node(db_session, label="UO2")
    await _seed_node(db_session, label="UO3")
    await _seed_node(db_session, label="UO4")

    response = await async_client.get("/api/v1/kg/search?q=U&limit=2")
    assert response.status_code == 200
    assert len(response.json()["data"]) <= 2
