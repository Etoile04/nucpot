"""Integration tests for /api/v1/lightrag proxy endpoints (NFM-1061).

Covers the 4 LightRAG sidecar proxy endpoints:
- POST /lightrag/ingest      — submit document for ingestion (returns 202)
- POST /lightrag/query       — semantic query (naive/local/global/hybrid modes)
- POST /lightrag/graph-query — Cypher graph query
- GET  /lightrag/health      — sidecar health check

Uses dependency injection via set_client() to inject mock/stub clients.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient

from nfm_db.schemas.lightrag import (
    GraphQueryResponse,
    HealthResponse,
    QueryMode,
    QueryResponse,
)
from nfm_db.services.lightrag_client import LightRAGClientProtocol

# ---------------------------------------------------------------------------
# Mock client — frozen dataclass implementing the protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MockLightRAGClient:
    """Test double implementing the LightRAG client protocol."""

    should_fail: bool = False
    error_message: str = "Sidecar unreachable"
    ingest_id: uuid.UUID = field(default_factory=uuid.uuid4)
    query_answer: str | None = "UO2 has a thermal conductivity of 13.5 W/(m·K)"
    query_sources: list[dict[str, Any]] | None = None
    graph_results: list[dict[str, Any]] | None = None
    health_status: str = "ok"
    health_version: str | None = "0.2.0"

    async def ingest(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> uuid.UUID:
        if self.should_fail:
            raise ConnectionError(self.error_message)
        return self.ingest_id

    async def query(
        self,
        query: str,
        mode: QueryMode = QueryMode.HYBRID,
    ) -> QueryResponse:
        if self.should_fail:
            raise ConnectionError(self.error_message)
        return QueryResponse(
            answer=self.query_answer,
            sources=self.query_sources or [],
        )

    async def graph_query(self, query: str) -> GraphQueryResponse:
        if self.should_fail:
            raise ConnectionError(self.error_message)
        return GraphQueryResponse(
            results=self.graph_results or [],
        )

    async def health(self) -> HealthResponse:
        if self.should_fail:
            raise ConnectionError(self.error_message)
        return HealthResponse(
            status=self.health_status,
            version=self.health_version,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> MockLightRAGClient:
    """Return a functioning mock LightRAG client."""
    return MockLightRAGClient()


@pytest.fixture
def failing_client() -> MockLightRAGClient:
    """Return a mock client that always raises ConnectionError."""
    return MockLightRAGClient(should_fail=True)


def _inject_client(client: LightRAGClientProtocol) -> None:
    """Replace the module-level LightRAG client via set_client()."""
    from nfm_db.api.v1 import lightrag as lr_module

    lr_module.set_client(client)


def _restore_stub() -> None:
    """Restore the default StubLightRAGClient after tests."""
    from nfm_db.api.v1 import lightrag as lr_module
    from nfm_db.services.lightrag_client import StubLightRAGClient

    lr_module.set_client(StubLightRAGClient())


# ---------------------------------------------------------------------------
# POST /api/v1/lightrag/ingest  (4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ingest_happy_path(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /ingest returns 202 with batch_id and accepted status."""
    _inject_client(mock_client)

    payload = {
        "text": "UO2 thermal conductivity 13.5 W/(m·K)",
        "metadata": {"source": "test", "doi": "10.1234/test"},
        "source_id": "src-001",
    }
    resp = await async_client.post("/api/v1/lightrag/ingest", json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "accepted"
    assert "batch_id" in body["data"]
    uuid.UUID(body["data"]["batch_id"])  # raises ValueError if invalid


@pytest.mark.integration
async def test_ingest_minimal_payload(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /ingest works with only required text field."""
    _inject_client(mock_client)

    resp = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"text": "some document text"},
    )

    assert resp.status_code == 202
    assert resp.json()["success"] is True
    assert resp.json()["data"]["status"] == "accepted"


@pytest.mark.integration
async def test_ingest_rejects_empty_text(async_client: AsyncClient):
    """POST /ingest rejects empty text with 422 validation error."""
    _restore_stub()

    resp = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"text": ""},
    )

    assert resp.status_code == 422


@pytest.mark.integration
async def test_ingest_503_on_sidecar_error(async_client: AsyncClient, failing_client: MockLightRAGClient):
    """POST /ingest returns 503 when sidecar raises an exception."""
    _inject_client(failing_client)

    resp = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"text": "test document"},
    )

    assert resp.status_code == 503
    body = resp.json()
    assert body["success"] is False
    assert "unavailable" in body["error"]


# ---------------------------------------------------------------------------
# POST /api/v1/lightrag/query  (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_query_happy_path(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /query returns answer and sources on success."""
    _inject_client(mock_client)

    resp = await async_client.post(
        "/api/v1/lightrag/query",
        json={
            "query": "What is the thermal conductivity of UO2?",
            "mode": "hybrid",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "UO2" in body["data"]["answer"]
    assert body["data"]["sources"] == []


@pytest.mark.integration
async def test_query_default_mode_is_hybrid(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /query defaults to hybrid mode when mode is omitted."""
    _inject_client(mock_client)

    resp = await async_client.post(
        "/api/v1/lightrag/query",
        json={"query": "test query"},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.integration
async def test_query_all_modes_accepted(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /query accepts all four query modes: naive, local, global, hybrid."""
    _inject_client(mock_client)

    for mode in ("naive", "local", "global", "hybrid"):
        resp = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": f"test query in {mode} mode", "mode": mode},
        )
        assert resp.status_code == 200, f"mode={mode} should return 200"
        assert resp.json()["success"] is True


@pytest.mark.integration
async def test_query_with_sources(async_client: AsyncClient):
    """POST /query returns populated sources list when client provides them."""
    client = MockLightRAGClient(
        query_sources=[
            {"chunk": "chunk-1", "score": 0.9},
            {"chunk": "chunk-2", "score": 0.8},
        ],
    )
    _inject_client(client)

    resp = await async_client.post(
        "/api/v1/lightrag/query",
        json={"query": "test"},
    )

    assert resp.status_code == 200
    sources = resp.json()["data"]["sources"]
    assert len(sources) == 2


@pytest.mark.integration
async def test_query_503_on_sidecar_error(async_client: AsyncClient, failing_client: MockLightRAGClient):
    """POST /query returns 503 when sidecar raises an exception."""
    _inject_client(failing_client)

    resp = await async_client.post(
        "/api/v1/lightrag/query",
        json={"query": "test query"},
    )

    assert resp.status_code == 503
    assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# POST /api/v1/lightrag/graph-query  (4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_graph_query_happy_path(async_client: AsyncClient):
    """POST /graph-query returns result rows on success."""
    client = MockLightRAGClient(
        graph_results=[
            {"n.name": "UO2", "n.type": "Material"},
            {"n.name": "ZrO2", "n.type": "Material"},
        ],
    )
    _inject_client(client)

    resp = await async_client.post(
        "/api/v1/lightrag/graph-query",
        json={"query": "MATCH (n:Material) RETURN n.name, n.type"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]["results"]) == 2
    assert body["data"]["results"][0]["n.name"] == "UO2"


@pytest.mark.integration
async def test_graph_query_empty_results(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """POST /graph-query returns empty results list when no matches."""
    _inject_client(mock_client)

    resp = await async_client.post(
        "/api/v1/lightrag/graph-query",
        json={"query": "MATCH (n:Material {name: 'NONEXISTENT'}) RETURN n"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["results"] == []


@pytest.mark.integration
async def test_graph_query_rejects_empty_query(async_client: AsyncClient):
    """POST /graph-query rejects empty query string with 422."""
    _restore_stub()

    resp = await async_client.post(
        "/api/v1/lightrag/graph-query",
        json={"query": ""},
    )

    assert resp.status_code == 422


@pytest.mark.integration
async def test_graph_query_503_on_sidecar_error(async_client: AsyncClient, failing_client: MockLightRAGClient):
    """POST /graph-query returns 503 when sidecar raises an exception."""
    _inject_client(failing_client)

    resp = await async_client.post(
        "/api/v1/lightrag/graph-query",
        json={"query": "MATCH (n) RETURN n"},
    )

    assert resp.status_code == 503
    assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# GET /api/v1/lightrag/health  (4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_ok(async_client: AsyncClient, mock_client: MockLightRAGClient):
    """GET /health returns ok with version when sidecar is healthy."""
    _inject_client(mock_client)

    resp = await async_client.get("/api/v1/lightrag/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["data"]["version"] == "0.2.0"


@pytest.mark.integration
async def test_health_unavailable_status(async_client: AsyncClient):
    """GET /health reflects unavailable sidecar status."""
    client = MockLightRAGClient(health_status="unavailable", health_version=None)
    _inject_client(client)

    resp = await async_client.get("/api/v1/lightrag/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "unavailable"
    assert body["data"]["version"] is None


@pytest.mark.integration
async def test_health_no_version(async_client: AsyncClient):
    """GET /health works when version is not provided."""
    client = MockLightRAGClient(health_status="ok", health_version=None)
    _inject_client(client)

    resp = await async_client.get("/api/v1/lightrag/health")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


@pytest.mark.integration
async def test_health_503_on_sidecar_error(async_client: AsyncClient, failing_client: MockLightRAGClient):
    """GET /health returns 503 when sidecar is unreachable."""
    _inject_client(failing_client)

    resp = await async_client.get("/api/v1/lightrag/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["success"] is False
    assert "unavailable" in body["error"]


# ---------------------------------------------------------------------------
# Cross-cutting: NFM API resilience when LightRAG is down
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_core_health_unaffected_by_lightrag_down(
    async_client: AsyncClient,
    failing_client: MockLightRAGClient,
):
    """Core /api/v1/health endpoint works even when LightRAG is down."""
    _inject_client(failing_client)

    resp = await async_client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
