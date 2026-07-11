"""Tests for KG Search API endpoint (NFM-1166).

Tests for GET /api/v1/kg/search covering:
- Basic search with query term
- No params returns all active nodes (paginated)
- Type filter validation rejects invalid types (400)
- Response schema matches contract
- Pagination works correctly
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.kg import router
from nfm_db.database import get_db


def _make_client(db_override=None) -> TestClient:
    """Create a TestClient with a real FastAPI app wrapping the KG router."""
    app = FastAPI()
    app.include_router(router)
    if db_override is not None:
        app.dependency_overrides[get_db] = db_override
    return TestClient(app)


def _make_node(
    node_type: str = "Material",
    label: str = "UO2",
    node_id: uuid.UUID | None = None,
    aliases: str | None = None,
    status: str = "active",
    confidence: float = 0.95,
    source_id: uuid.UUID | None = None,
) -> MagicMock:
    """Create a mock KGNode with common fields."""
    return MagicMock(
        id=node_id or uuid.uuid4(),
        node_type=node_type,
        label=label,
        aliases=aliases,
        properties={"formula": "UO2"},
        confidence=confidence,
        status=status,
        source_id=source_id,
    )


def _mock_session_side_effect(total: int, nodes: list) -> list:
    """Build the side_effect list for mock_session.execute."""
    data_result = MagicMock()
    data_result.scalars.return_value.all.return_value = nodes
    return [
        MagicMock(scalar_one=MagicMock(return_value=total)),
        data_result,
    ]


class TestKGSearchBasic:
    """Tests for basic search functionality."""

    @pytest.mark.asyncio
    async def test_search_with_query(self) -> None:
        """GET /api/v1/kg/search?q=test&type=Material returns matching results."""
        node = _make_node(label="Test Material")

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(1, [node])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search", params={"q": "test", "type": "Material"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["label"] == "Test Material"
        assert body["items"][0]["node_type"] == "Material"

    @pytest.mark.asyncio
    async def test_search_no_params_returns_active(self) -> None:
        """GET /api/v1/kg/search without params returns all active nodes (paginated)."""
        node = _make_node(label="UO2")

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(1, [node])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["limit"] == 20
        assert body["offset"] == 0


class TestKGSearchTypeValidation:
    """Tests for type filter validation."""

    @pytest.mark.asyncio
    async def test_invalid_type_returns_400(self) -> None:
        """Type filter must reject invalid types with 400."""
        mock_session = AsyncMock(spec=AsyncSession)
        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search", params={"type": "InvalidType"})

        assert resp.status_code == 400
        assert "Invalid node_type" in resp.json()["detail"]
        assert "InvalidType" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "valid_type", ["Material", "Property", "Experiment", "Condition", "Publication"]
    )
    async def test_valid_types_accepted(self, valid_type: str) -> None:
        """All valid node types should be accepted without error."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(0, [])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search", params={"type": valid_type})

        assert resp.status_code == 200


class TestKGSearchResponseSchema:
    """Tests for response schema contract compliance."""

    @pytest.mark.asyncio
    async def test_response_matches_contract(self) -> None:
        """Response schema matches the NFM-1166 contract."""
        node_id = uuid.uuid4()
        source_id = uuid.uuid4()
        node = _make_node(
            node_id=node_id,
            label="UO2",
            aliases='["Uranium Dioxide", "UO2"]',
            status="active",
            confidence=0.95,
            source_id=source_id,
        )

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(1, [node])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search")

        assert resp.status_code == 200
        body = resp.json()

        # Top-level envelope
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

        # Item structure
        item = body["items"][0]
        assert item["id"] == str(node_id)
        assert item["node_type"] == "Material"
        assert item["label"] == "UO2"
        assert item["aliases"] == ["Uranium Dioxide", "UO2"]
        assert item["properties"] == {"formula": "UO2"}
        assert item["confidence"] == 0.95
        assert item["status"] == "active"
        assert item["source_id"] == str(source_id)

    @pytest.mark.asyncio
    async def test_null_source_id(self) -> None:
        """source_id should be null when not set."""
        node = _make_node(source_id=None)

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(1, [node])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search")

        assert resp.status_code == 200
        assert resp.json()["items"][0]["source_id"] is None


class TestKGSearchPagination:
    """Tests for pagination behavior."""

    @pytest.mark.asyncio
    async def test_custom_limit_and_offset(self) -> None:
        """Custom limit and offset should be reflected in response."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(0, [])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search", params={"limit": 50, "offset": 10})

        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 50
        assert body["offset"] == 10

    @pytest.mark.asyncio
    async def test_empty_aliases_parsed_as_empty_list(self) -> None:
        """Null aliases should return empty list, not cause errors."""
        node = _make_node(aliases=None)

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(
            side_effect=_mock_session_side_effect(1, [node])
        )

        client = _make_client(lambda: mock_session)
        resp = client.get("/kg/search")

        assert resp.status_code == 200
        assert resp.json()["items"][0]["aliases"] == []
