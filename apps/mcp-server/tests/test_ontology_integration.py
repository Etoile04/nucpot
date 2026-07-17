"""Integration tests for ontology MCP tools (Phase B).

Tests that browse_ontology produces correctly-shaped JSON when backed
by the real derive_ontology_graph service.  The DB session and service
layer are both mocked to isolate the MCP tool logic.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ── Helpers ──────────────────────────────────────────────────────


def _make_session_gen():
    """Create a callable that returns an async generator yielding a mock session."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    async def _gen() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _gen


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_graph(
    corpus_id: str = "test-corpus",
    nodes: list | None = None,
    relationships: list | None = None,
) -> object:
    """Create a fake OntologyGraphResponse (lazy imports inside function)."""
    from nfm_db.schemas.ontology import (
        CONTRACT_SCHEMA_VERSION,
        OntologyGraphResponse,
        OntologyNode,
        OntologyRelationship,
        OntologyStats,
    )

    if nodes is None:
        nodes = [
            OntologyNode(
                id="mat:UO2",
                type="individual",
                name="UO2",
                label="UO2",
                record_ref="/materials/UO2?corpus=test-corpus",
            ),
            OntologyNode(
                id="prop:lattice_constant",
                type="class",
                name="lattice_constant",
                label="lattice_constant",
                record_ref=None,
            ),
            OntologyNode(
                id="src:test-corpus",
                type="class",
                name="test-corpus",
                label="test-corpus",
                record_ref=None,
            ),
        ]
    if relationships is None:
        relationships = [
            OntologyRelationship(
                id="mat:UO2|HAS_PROPERTY|prop:lattice_constant",
                from_="mat:UO2",
                to="prop:lattice_constant",
                type="HAS_PROPERTY",
            ),
            OntologyRelationship(
                id="prop:lattice_constant|MEASURED_BY|method:DFT",
                from_="prop:lattice_constant",
                to="method:DFT",
                type="MEASURED_BY",
            ),
        ]
    return OntologyGraphResponse(
        schema_version=CONTRACT_SCHEMA_VERSION,
        corpus_id=corpus_id,
        generated_at=_now(),
        source_ontology="nfmd/ref-gap-fill",
        source_digest="abcdef1234567890",
        stats=OntologyStats(
            nodes=len(nodes),
            relationships=len(relationships),
            classes=sum(1 for n in nodes if n.type == "class"),
            individuals=sum(1 for n in nodes if n.type == "individual"),
        ),
        nodes=nodes,
        relationships=relationships,
        pagination=None,
    )


# ── browse_ontology ────────────────────────────────────────────


class TestBrowseOntologyTool:
    """Integration tests for the browse_ontology MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_graph_json(self) -> None:
        """browse_ontology should return JSON with nodes and relationships."""
        from nfm_mcp.server import create_mcp_server

        graph = _make_graph()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                return_value=graph,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(parent_id="test-corpus")

        result = json.loads(result_str)
        assert "nodes" in result
        assert "relationships" in result
        assert result["corpus_id"] == "test-corpus"
        assert len(result["nodes"]) == 3

    @pytest.mark.asyncio
    async def test_returns_error_when_parent_id_missing(self) -> None:
        """browse_ontology should return error when parent_id is not provided."""
        from nfm_mcp.server import create_mcp_server

        mcp = create_mcp_server()
        tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
        result_str = await tool_fn()

        result = json.loads(result_str)
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_handles_corpus_not_found(self) -> None:
        """browse_ontology should return error when corpus has no staging rows."""
        from nfm_mcp.server import create_mcp_server

        from nfm_db.services.ontology_service import CorpusNotFoundError

        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                side_effect=CorpusNotFoundError("missing-corpus"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(parent_id="missing-corpus")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_filters_by_entity_type(self) -> None:
        """browse_ontology should post-filter nodes by entity_type."""
        from nfm_mcp.server import create_mcp_server

        graph = _make_graph()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                return_value=graph,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(parent_id="test-corpus", entity_type="class")

        result = json.loads(result_str)
        # All returned nodes should be of type "class"
        for node in result["nodes"]:
            assert node["type"] == "class"

    @pytest.mark.asyncio
    async def test_filters_by_query(self) -> None:
        """browse_ontology should post-filter nodes by search query."""
        from nfm_mcp.server import create_mcp_server

        graph = _make_graph()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                return_value=graph,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(parent_id="test-corpus", query="UO2")

        result = json.loads(result_str)
        # Should only contain nodes matching "uo2" (case-insensitive)
        assert all(
            "uo2" in n.get("label", "").lower()
            or "uo2" in n.get("name", "").lower()
            or "uo2" in n.get("id", "").lower()
            for n in result["nodes"]
        )

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """browse_ontology should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(parent_id="test-corpus")

        result = json.loads(result_str)
        assert "error" in result
        assert "Ontology lookup failed" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_limit_to_service(self) -> None:
        """browse_ontology should forward limit as max_nodes to service."""
        from nfm_mcp.server import create_mcp_server

        graph = _make_graph()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                return_value=graph,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            await tool_fn(parent_id="test-corpus", limit=25)

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["max_nodes"] == 25
