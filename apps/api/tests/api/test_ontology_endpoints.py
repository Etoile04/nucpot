"""Tests for ontology API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from nfm_db.schemas.ontology import (
    OntologyGraphResponse,
    OntologyNode,
    OntologyPagination,
    OntologyRelationship,
    OntologyStats,
)
from nfm_db.services.ontology_service import CorpusNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_graph_response(
    *,
    corpus_id: str = "test-corpus",
    source_digest: str = "a1b2c3d4e5f6a7b8",
    has_pagination: bool = False,
    last_modified: datetime | None = None,
) -> OntologyGraphResponse:
    """Build a valid OntologyGraphResponse for mocking.

    ``source_digest`` must be exactly 16 lowercase hex chars to pass
    the schema pattern ``^[a-f0-9]{16}$``.
    """
    graph = OntologyGraphResponse(
        schema_version="1.1",
        corpus_id=corpus_id,
        generated_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        source_ontology="ontofuel-v0.1",
        source_digest=source_digest,
        stats=OntologyStats(
            nodes=5,
            relationships=8,
            classes=2,
            individuals=3,
        ),
        nodes=[
            OntologyNode(
                id="node1",
                type="individual",
                name="UO2",
                label="Uranium Dioxide",
            ),
            OntologyNode(
                id="node2",
                type="class",
                name="lattice_constant",
                label="Lattice Constant",
            ),
        ],
        relationships=[
            OntologyRelationship(
                id="rel1",
                from_="node1",
                to="node2",
                type="related_to",
                label="has property",
            ),
        ],
        pagination=(OntologyPagination(next_cursor="abc123", total=10) if has_pagination else None),
    )
    if last_modified is not None:
        graph._last_modified = last_modified
    return graph


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 200 success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_200_success(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
) -> None:
    """Successful request returns 200, correct body, ETag and Cache-Control."""

    mock_graph = _build_graph_response(corpus_id="smirnov2014")
    mock_derive.return_value = mock_graph

    response = await async_client.get(
        "/api/v1/ontology/corpora/smirnov2014/graph",
    )

    assert response.status_code == 200, response.text

    data = response.json()

    # Verify core envelope fields
    assert data["schema_version"] == "1.1"
    assert data["corpus_id"] == "smirnov2014"
    assert data["source_ontology"] == "ontofuel-v0.1"
    assert data["source_digest"] == "a1b2c3d4e5f6a7b8"

    # Verify stats
    stats = data["stats"]
    assert stats["nodes"] == 5
    assert stats["relationships"] == 8
    assert stats["classes"] == 2
    assert stats["individuals"] == 3

    # Verify node data
    assert len(data["nodes"]) == 2
    assert data["nodes"][0]["id"] == "node1"
    assert data["nodes"][0]["type"] == "individual"
    assert data["nodes"][1]["id"] == "node2"
    assert data["nodes"][1]["type"] == "class"

    # Verify relationship uses the 'from' alias (not 'from_')
    assert len(data["relationships"]) == 1
    assert "from" in data["relationships"][0]
    assert "from_" not in data["relationships"][0]
    assert data["relationships"][0]["from"] == "node1"
    assert data["relationships"][0]["to"] == "node2"
    assert data["relationships"][0]["type"] == "related_to"

    # No pagination when not paginated
    assert data["pagination"] is None

    # Verify ETag header (without cursor)
    assert response.headers["ETag"] == '"a1b2c3d4e5f6a7b8"'

    # Verify Cache-Control header
    assert response.headers["Cache-Control"] == "public, max-age=60"

    # Verify service was called with correct arguments
    mock_derive.assert_awaited_once()
    call_args = mock_derive.call_args
    # corpus_id is a positional arg; session, corpus_id, max_nodes, cursor
    assert call_args[0][1] == "smirnov2014"
    assert call_args[1]["max_nodes"] is None
    assert call_args[1]["cursor"] is None


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 200 with pagination cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_200_with_pagination_cursor(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
) -> None:
    """Paginated request folds cursor into the ETag value."""

    mock_graph = _build_graph_response(
        corpus_id="smirnov2014",
        has_pagination=True,
    )
    mock_derive.return_value = mock_graph

    response = await async_client.get(
        "/api/v1/ontology/corpora/smirnov2014/graph",
        params={"cursor": "xyz789"},
    )

    assert response.status_code == 200, response.text
    data = response.json()

    # Pagination present in body
    assert data["pagination"] is not None
    assert data["pagination"]["next_cursor"] == "abc123"
    assert data["pagination"]["total"] == 10

    # ETag includes cursor fragment
    assert response.headers["ETag"] == '"a1b2c3d4e5f6a7b8#xyz789"'

    # Cache-Control still present
    assert response.headers["Cache-Control"] == "public, max-age=60"

    # Service called with cursor
    call_kwargs = mock_derive.call_args[1]
    assert call_kwargs["cursor"] == "xyz789"


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 200 with max_nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_200_with_max_nodes(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
) -> None:
    """max_nodes query param is forwarded to the service layer."""

    mock_graph = _build_graph_response()
    mock_derive.return_value = mock_graph

    response = await async_client.get(
        "/api/v1/ontology/corpora/smirnov2014/graph",
        params={"max_nodes": "100"},
    )

    assert response.status_code == 200, response.text

    # Verify max_nodes was passed through
    call_kwargs = mock_derive.call_args[1]
    assert call_kwargs["max_nodes"] == 100


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 200 with Last-Modified header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_200_last_modified_header(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
) -> None:
    """When _last_modified is set, the Last-Modified header is emitted."""

    last_modified = datetime(2025, 3, 20, 14, 30, 0, tzinfo=UTC)
    mock_graph = _build_graph_response(last_modified=last_modified)
    mock_derive.return_value = mock_graph

    response = await async_client.get(
        "/api/v1/ontology/corpora/smirnov2014/graph",
    )

    assert response.status_code == 200, response.text

    # Last-Modified header must be present
    assert "Last-Modified" in response.headers
    header_val = response.headers["Last-Modified"]
    # RFC 7231 format: "Day, DD Mon YYYY HH:MM:SS GMT"
    assert "GMT" in header_val
    assert "Mar" in header_val  # month
    assert "2025" in header_val  # year


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 404 corpus not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_404_not_found(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
) -> None:
    """CorpusNotFoundError from the service maps to HTTP 404."""

    mock_derive.side_effect = CorpusNotFoundError("nonexistent")

    response = await async_client.get(
        "/api/v1/ontology/corpora/nonexistent/graph",
    )

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 422 malformed corpus_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_corpus_id",
    [
        "-starts-with-hyphen",  # starts with hyphen
        ".starts-with-dot",  # starts with dot
        "has spaces!",  # invalid characters (space + exclamation)
        "has@at-sign",  # at-sign not allowed
        "a" * 65,  # exceeds 64-char ceiling
    ],
)
@patch("nfm_db.api.v1.ontology.derive_ontology_graph", new_callable=AsyncMock)
async def test_get_corpus_graph_422_malformed_corpus_id(
    mock_derive: AsyncMock,
    async_client: AsyncClient,
    bad_corpus_id: str,
) -> None:
    """Malformed corpus_id is rejected with 422 before the service is called."""

    response = await async_client.get(
        f"/api/v1/ontology/corpora/{bad_corpus_id}/graph",
    )

    assert response.status_code == 422, (
        f"Expected 422 for corpus_id={bad_corpus_id!r}, got {response.status_code}: {response.text}"
    )
    # Service must NOT be invoked for malformed corpus IDs
    mock_derive.assert_not_awaited()
