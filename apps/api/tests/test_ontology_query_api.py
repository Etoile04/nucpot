"""Tests for Phase 2 Ontology Query API Endpoints (NFM-820).

Tests for the 4 new AGE-backed ontology endpoints:
- GET /api/v1/ontology/node/{id} - node + neighbors
- GET /api/v1/ontology/search - fuzzy search
- GET /api/v1/ontology/path - shortest path
- POST /api/v1/ontology/sync - graph rebuild

Coverage target: ≥80% for NFM-820 acceptance criteria.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.ontology import router
from nfm_db.database import get_db
from nfm_db.models.kg import KGNode


def _make_client(db_override=None) -> TestClient:
    """Create a TestClient with a real FastAPI app wrapping the router."""
    app = FastAPI()
    app.include_router(router)
    if db_override is not None:
        app.dependency_overrides[get_db] = db_override
    return TestClient(app)


# ---------------------------------------------------------------------------
# Node Neighbors
# ---------------------------------------------------------------------------


class TestGetNodeNeighbors:
    """Tests for GET /ontology/node/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_node_neighbors_success(self) -> None:
        """Test successful node neighbors retrieval."""
        node_id = uuid.uuid4()

        mock_node = KGNode(
            id=node_id,
            node_type="Material",
            label="UO2",
            corpus_id="test-corpus",
        )

        mock_session = AsyncMock(spec=AsyncSession)

        with patch("nfm_db.api.v1.ontology.select"):
            node_result = MagicMock()
            node_result.scalar_one_or_none.return_value = mock_node
            node_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=node_result)

            client = _make_client(lambda: mock_session)
            response = client.get(f"/ontology/node/{node_id}?depth=1")

            assert response.status_code == 200
            data = response.json()
            assert "node" in data
            assert "neighbors" in data
            assert "total_neighbors" in data

    @pytest.mark.asyncio
    async def test_get_node_neighbors_not_found(self) -> None:
        """Test 404 when node not found."""
        node_id = uuid.uuid4()

        mock_session = AsyncMock(spec=AsyncSession)
        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=node_result)

        client = _make_client(lambda: mock_session)
        response = client.get(f"/ontology/node/{node_id}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_node_neighbors_invalid_depth(self) -> None:
        """Test validation error for invalid depth parameter."""
        node_id = uuid.uuid4()
        client = _make_client()
        response = client.get(f"/ontology/node/{node_id}?depth=5")

        # FastAPI validation should reject depth > 3
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Search Nodes
# ---------------------------------------------------------------------------


class TestSearchNodes:
    """Tests for GET /ontology/search endpoint."""

    @pytest.mark.asyncio
    async def test_search_nodes_success(self) -> None:
        """Test successful node search."""
        mock_nodes = [
            KGNode(
                id=uuid.uuid4(),
                node_type="Material",
                label="Uranium Dioxide",
                corpus_id="nucpot",
            ),
            KGNode(
                id=uuid.uuid4(),
                node_type="Material",
                label="Uranium",
                corpus_id="nucpot",
            ),
        ]

        mock_session = AsyncMock(spec=AsyncSession)

        with patch("nfm_db.api.v1.ontology.select"):
            count_result = MagicMock()
            count_result.all.return_value = [2]

            search_result = MagicMock()
            search_result.scalars.return_value.all.return_value = mock_nodes

            def execute_side_effect(query):
                if "count" in str(query).lower():
                    return count_result
                return search_result

            mock_session.execute = AsyncMock(side_effect=execute_side_effect)

            client = _make_client(lambda: mock_session)
            response = client.get("/ontology/search?q=Uranium")

            assert response.status_code == 200
            data = response.json()
            assert "results" in data
            assert "total" in data
            assert "limit" in data
            assert "offset" in data

    @pytest.mark.asyncio
    async def test_search_nodes_pagination(self) -> None:
        """Test pagination parameters work correctly."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(return_value=MagicMock())

        client = _make_client(lambda: mock_session)
        response = client.get("/ontology/search?q=test&limit=10&offset=5")

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 5


# ---------------------------------------------------------------------------
# Shortest Path
# ---------------------------------------------------------------------------


class TestGetShortestPath:
    """Tests for GET /ontology/path endpoint."""

    @pytest.mark.asyncio
    async def test_get_shortest_path_success(self) -> None:
        """Test successful shortest path calculation."""
        from_id = uuid.uuid4()
        to_id = uuid.uuid4()

        mock_from_node = KGNode(
            id=from_id,
            node_type="Material",
            label="UO2",
            corpus_id="nucpot",
        )

        mock_to_node = KGNode(
            id=to_id,
            node_type="Property",
            label="thermal_conductivity",
            corpus_id="nucpot",
        )

        mock_session = AsyncMock(spec=AsyncSession)

        with patch("nfm_db.api.v1.ontology.select"):
            from_result = MagicMock()
            from_result.scalar_one_or_none.return_value = mock_from_node

            to_result = MagicMock()
            to_result.scalar_one_or_none.return_value = mock_to_node

            call_idx = {"n": 0}

            def execute_side_effect(query):
                call_idx["n"] += 1
                return from_result if call_idx["n"] % 2 == 1 else to_result

            mock_session.execute = AsyncMock(side_effect=execute_side_effect)

            client = _make_client(lambda: mock_session)
            response = client.get(f"/ontology/path?from={from_id}&to={to_id}")

            assert response.status_code == 200
            data = response.json()
            assert "from_node" in data
            assert "to_node" in data
            assert "path" in data
            assert "length" in data

    @pytest.mark.asyncio
    async def test_get_shortest_path_node_not_found(self) -> None:
        """Test 404 when one or both nodes not found."""
        from_id = uuid.uuid4()
        to_id = uuid.uuid4()

        mock_session = AsyncMock(spec=AsyncSession)

        with patch("nfm_db.api.v1.ontology.select"):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=result)

            client = _make_client(lambda: mock_session)
            response = client.get(f"/ontology/path?from={from_id}&to={to_id}")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Sync Corpus Graph
# ---------------------------------------------------------------------------


class TestSyncCorpusGraph:
    """Tests for POST /ontology/sync endpoint."""

    @pytest.mark.asyncio
    async def test_sync_graph_success(self) -> None:
        """Test successful graph sync."""
        corpus_id = "test-corpus"

        with patch(
            "nfm_db.api.v1.ontology.rebuild_graph", new_callable=AsyncMock
        ) as mock_rebuild:
            from nfm_db.services.ontology_sync import SyncResult
            mock_rebuild.return_value = SyncResult(
                nodes_synced=150,
                edges_synced=320,
                duration_ms=450.0,
            )

            mock_session = AsyncMock(spec=AsyncSession)
            client = _make_client(lambda: mock_session)
            response = client.post(f"/ontology/sync?corpus_id={corpus_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["corpus_id"] == corpus_id
            assert data["nodes_synced"] == 150
            assert data["edges_synced"] == 320
            assert data["duration_ms"] == 450.0

    @pytest.mark.asyncio
    async def test_sync_graph_not_found(self) -> None:
        """Test 404 when graph doesn't exist."""
        from nfm_db.services.ontology_sync import GraphNotFoundError

        corpus_id = "test-corpus"

        with patch(
            "nfm_db.api.v1.ontology.rebuild_graph", new_callable=AsyncMock
        ) as mock_rebuild:
            mock_rebuild.side_effect = GraphNotFoundError("graph not found")

            mock_session = AsyncMock(spec=AsyncSession)
            client = _make_client(lambda: mock_session)
            response = client.post(f"/ontology/sync?corpus_id={corpus_id}")

            assert response.status_code == 404
            assert "graph not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_sync_graph_sync_error(self) -> None:
        """Test 500 when sync fails."""
        from nfm_db.services.ontology_sync import OntologySyncError

        corpus_id = "test-corpus"

        with patch(
            "nfm_db.api.v1.ontology.rebuild_graph", new_callable=AsyncMock
        ) as mock_rebuild:
            mock_rebuild.side_effect = OntologySyncError("sync failed")

            mock_session = AsyncMock(spec=AsyncSession)
            client = _make_client(lambda: mock_session)
            response = client.post(f"/ontology/sync?corpus_id={corpus_id}")

            assert response.status_code == 500
            assert "sync failed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(router)


@pytest.fixture
def mock_session() -> AsyncSession:
    """Create a mock AsyncSession for testing."""
    return AsyncMock(spec=AsyncSession)
