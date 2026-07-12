"""HTTP integration tests for the three KG query endpoints (NFM-858).

These tests exercise the FastAPI routers in ``apps/api/src/nfm_db/api/v1/kg.py``
through ``AsyncClient`` against the in-memory SQLite test database.
They verify:

* the request schema validators are wired (L2) — invalid ``direction``
  and over-cap ``max_depth`` return HTTP 422 with field-level errors,
* the three new endpoints expose the expected JSON shape,
* error mapping for service-level ``ValueError`` (HTTP 400).
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode


async def _seed_node(
    db_session: AsyncSession,
    *,
    node_type: str = "Material",
    label: str = "UO2",
    properties: dict | None = None,
) -> KGNode:
    node = KGNode(
        node_type=node_type,
        label=label,
        aliases=json.dumps([]),
        properties=properties or {},
        confidence=0.95,
        status="active",
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def _seed_edge(
    db_session: AsyncSession,
    source: KGNode,
    target: KGNode,
    relation_type: str = "hasProperty",
) -> KGEdge:
    edge = KGEdge(
        source_node_id=source.id,
        target_node_id=target.id,
        relation_type=relation_type,
        properties={},
        confidence=0.9,
    )
    db_session.add(edge)
    await db_session.commit()
    await db_session.refresh(edge)
    return edge


# ---------------------------------------------------------------------------
# GET /api/v1/kg/query/property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_property_query_endpoint_returns_matching_nodes(
    async_client, db_session,
) -> None:
    """L2: ``GET /kg/query/property`` returns nodes matching the JSON filter."""
    await _seed_node(db_session, label="UO2", properties={"density": "10.97"})
    await _seed_node(db_session, label="MOX", properties={"density": "11.0"})

    response = await async_client.get(
        "/api/v1/kg/query/property",
        params={"property_key": "density", "property_value": "10.97"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["nodes"][0]["label"] == "UO2"


@pytest.mark.asyncio
async def test_property_query_endpoint_fuzzy_label(
    async_client, db_session,
) -> None:
    """``GET /kg/query/property?fuzzy=true&label=UO2`` matches substrings."""
    await _seed_node(db_session, label="UO2")
    await _seed_node(db_session, label="UO2-Shell")

    response = await async_client.get(
        "/api/v1/kg/query/property",
        params={"label": "UO2", "fuzzy": "true"},
    )

    assert response.status_code == 200
    labels = sorted(n["label"] for n in response.json()["nodes"])
    assert labels == ["UO2", "UO2-Shell"]


@pytest.mark.asyncio
async def test_property_query_endpoint_limit_above_100_rejected(
    async_client,
) -> None:
    """L2: ``limit=200`` violates ``le=100`` — endpoint returns 422."""
    response = await async_client.get(
        "/api/v1/kg/query/property",
        params={"limit": "200"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/kg/query/relations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relation_query_endpoint_outgoing(
    async_client, db_session,
) -> None:
    """``GET /kg/query/relations`` returns outgoing edges from the source."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")
    await _seed_edge(db_session, a, b, relation_type="hasProperty")

    response = await async_client.get(
        "/api/v1/kg/query/relations",
        params={"source_node_id": str(a.id), "direction": "outgoing"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["edges"][0]["relation_type"] == "hasProperty"


@pytest.mark.asyncio
async def test_relation_query_endpoint_invalid_direction_rejected(
    async_client,
) -> None:
    """M2/L2: ``direction=upstream`` (not a Literal) returns 422."""
    response = await async_client.get(
        "/api/v1/kg/query/relations",
        params={"direction": "upstream"},
    )
    assert response.status_code == 422
    body = response.json()
    detail = body["detail"] if isinstance(body["detail"], list) else []
    assert any(
        "direction" in str(d.get("loc", []))
        for d in detail
    )


@pytest.mark.asyncio
async def test_relation_query_endpoint_invalid_relation_type_returns_empty(
    async_client, db_session,
) -> None:
    """Unknown ``relation_type`` is mapped to an empty response (not an error)."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")
    await _seed_edge(db_session, a, b, relation_type="hasProperty")

    response = await async_client.get(
        "/api/v1/kg/query/relations",
        params={"relation_type": "not_a_real_type"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["edges"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/kg/query/path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_query_endpoint_returns_two_hop_path(
    async_client, db_session,
) -> None:
    """``POST /kg/query/path`` returns a path with two edges."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")
    c = await _seed_node(db_session, label="C")
    await _seed_edge(db_session, a, b, relation_type="hasProperty")
    await _seed_edge(db_session, b, c, relation_type="measuredIn")

    response = await async_client.post(
        "/api/v1/kg/query/path",
        json={
            "source_node_id": str(a.id),
            "target_node_id": str(c.id),
            "max_depth": 3,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    path = body["paths"][0]
    assert path["length"] == 2
    assert [n["label"] for n in path["nodes"]] == ["A", "B", "C"]
    assert path["edges"][0]["relation_type"] == "hasProperty"
    assert path["edges"][1]["relation_type"] == "measuredIn"


@pytest.mark.asyncio
async def test_path_query_endpoint_max_depth_above_3_rejected(
    async_client,
) -> None:
    """M1/L2: ``max_depth=10`` violates ``le=3`` — endpoint returns 422."""
    response = await async_client.post(
        "/api/v1/kg/query/path",
        json={
            "source_node_id": str(uuid.uuid4()),
            "target_node_id": str(uuid.uuid4()),
            "max_depth": 10,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_path_query_endpoint_unknown_relation_type_returns_400(
    async_client, db_session,
) -> None:
    """H1: ``relation_types=['not_real']`` is mapped to 400 by the endpoint."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")

    response = await async_client.post(
        "/api/v1/kg/query/path",
        json={
            "source_node_id": str(a.id),
            "target_node_id": str(b.id),
            "max_depth": 3,
            "relation_types": ["not_a_relation"],
        },
    )
    assert response.status_code == 400
    assert "unknown relation_types" in response.text


@pytest.mark.asyncio
async def test_path_query_endpoint_disconnected_nodes_returns_empty(
    async_client, db_session,
) -> None:
    """``POST /kg/query/path`` for two disconnected nodes returns 200 + empty."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")

    response = await async_client.post(
        "/api/v1/kg/query/path",
        json={
            "source_node_id": str(a.id),
            "target_node_id": str(b.id),
            "max_depth": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_path_query_endpoint_default_max_depth_is_3(
    async_client, db_session,
) -> None:
    """Omitting ``max_depth`` uses the schema default of 3."""
    a = await _seed_node(db_session, label="A")
    b = await _seed_node(db_session, label="B")
    await _seed_edge(db_session, a, b, relation_type="hasProperty")

    response = await async_client.post(
        "/api/v1/kg/query/path",
        json={
            "source_node_id": str(a.id),
            "target_node_id": str(b.id),
        },
    )
    assert response.status_code == 200
