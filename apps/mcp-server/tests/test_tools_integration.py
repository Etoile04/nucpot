"""Integration tests for 5 wired MCP tool domains (Phase B).

Tests that search_sources, query_potentials, browse_ontology,
query_knowledge_graph, trigger_extraction, and get_extraction_status
produce correctly-shaped JSON when backed by real service calls.
DB session and service layer are both mocked to isolate tool logic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ── Helpers ──────────────────────────────────────────────────────


def _make_session_gen():
    """Create a callable that returns an async generator yielding a mock session.

    Patches get_db_session which is called as ``async for db in get_db_session()``.
    """
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    async def _gen() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _gen


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── search_sources ───────────────────────────────────────────────


class TestSearchSourcesTool:
    """Integration tests for the search_sources MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_paginated_sources(self) -> None:
        """search_sources should return JSON with items array from service."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_db.schemas.source import DataSourceResponse
        from nfm_mcp.server import create_mcp_server

        now = _now()
        source = DataSourceResponse(
            id=uuid.uuid4(),
            doi="10.1016/j.nucengdes.2020.110799",
            title="UO2 Thermal Conductivity",
            journal="Journal of Nuclear Engineering",
            year=2020,
            volume="370",
            pages="1-15",
            source_type="journal",
            abstract=None,
            external_url=None,
            created_at=now,
            updated_at=now,
        )
        expected = PaginatedResponse(
            items=[source],
            total=1,
            page=1,
            limit=20,
            pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        mock_svc.assert_called_once()
        result = json.loads(result_str)
        assert "items" in result
        assert result["total"] == 1
        assert result["items"][0]["title"] == "UO2 Thermal Conductivity"

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """search_sources should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        result = json.loads(result_str)
        assert "error" in result
        assert "Source search failed" in result["error"]

    @pytest.mark.asyncio
    async def test_post_filters_by_query_text(self) -> None:
        """search_sources should filter results by query text on title."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_db.schemas.source import DataSourceResponse
        from nfm_mcp.server import create_mcp_server

        now = _now()
        sources = [
            DataSourceResponse(
                id=uuid.uuid4(),
                title="Zirconium Alloy Properties",
                journal="J. Nucl. Mater.",
                year=2019,
                doi=None,
                volume=None,
                pages=None,
                source_type="journal",
                abstract=None,
                external_url=None,
                created_at=now,
                updated_at=now,
            ),
            DataSourceResponse(
                id=uuid.uuid4(),
                title="Silicon Carbide Review",
                journal="Ann. Nucl. Energy",
                year=2021,
                doi=None,
                volume=None,
                pages=None,
                source_type="report",
                abstract=None,
                external_url=None,
                created_at=now,
                updated_at=now,
            ),
        ]
        expected = PaginatedResponse(
            items=sources,
            total=2,
            page=1,
            limit=20,
            pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=expected,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            result_str = await tool_fn(query="Zirconium", limit=20, offset=0)

        result = json.loads(result_str)
        assert len(result["items"]) == 1
        assert "Zirconium" in result["items"][0]["title"]


# ── query_potentials ────────────────────────────────────────────


class TestQueryPotentialsTool:
    """Integration tests for the query_potentials MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_potentials_list(self) -> None:
        """query_potentials should return JSON from list_potentials service."""
        from nfm_db.schemas.potential import PotentialListResponse, PotentialSummary
        from nfm_mcp.server import create_mcp_server

        now = _now()
        potential = PotentialSummary(
            id=uuid.uuid4(),
            name="FINK-LUCUTA2",
            display_name="Fink-Lucuta UO2 Model",
            type="Gibbs",
            description="Gibbs energy model for UO2",
            created_at=now,
            updated_at=now,
        )
        expected = PotentialListResponse(
            potentials=[potential],
            total=1,
            page=1,
            limit=100,
            total_pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_potentials"].fn
            result_str = await tool_fn(
                material_id="UO2",
                potential_type="Gibbs",
            )

        mock_svc.assert_called_once()
        result = json.loads(result_str)
        assert "potentials" in result
        assert result["potentials"][0]["name"] == "FINK-LUCUTA2"

    @pytest.mark.asyncio
    async def test_handles_service_error(self) -> None:
        """query_potentials should return JSON error on failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB error"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_potentials"].fn
            result_str = await tool_fn(material_id="UO2")

        result = json.loads(result_str)
        assert "error" in result
        assert "Potential query failed" in result["error"]


# ── browse_ontology ───────────────────────────────────────────


class TestBrowseOntologyTool:
    """Integration tests for the browse_ontology MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_ontology_graph(self) -> None:
        """browse_ontology should return nodes and relationships."""
        from nfm_db.schemas.ontology import (
            OntologyGraphResponse,
            OntologyNode,
            OntologyRelationship,
            OntologyStats,
        )
        from nfm_mcp.server import create_mcp_server

        now = _now()
        node = OntologyNode(
            id="mat:UO2",
            type="individual",
            name="Uranium Dioxide",
            label="UO2",
        )
        relationship = OntologyRelationship(
            id="rel:1",
            from_="mat:UO2",
            to="prop:thermal_conductivity",
            type="hasProperty",
            label="has thermal conductivity",
        )
        expected = OntologyGraphResponse(
            corpus_id="nfmd/ref-gap-fill",
            generated_at=now,
            source_ontology="nfmd/ref-gap-fill",
            source_digest="a" * 16,
            stats=OntologyStats(
                nodes=1,
                relationships=1,
                classes=0,
                individuals=1,
            ),
            nodes=[node],
            relationships=[relationship],
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                return_value=expected,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(limit=50)

        result = json.loads(result_str)
        assert "nodes" in result
        assert "relationships" in result
        assert "stats" in result
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "mat:UO2"

    @pytest.mark.asyncio
    async def test_handles_service_error(self) -> None:
        """browse_ontology should return JSON error on failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.ontology.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.ontology_service.derive_ontology_graph",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Ontology service error"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["browse_ontology"].fn
            result_str = await tool_fn(query="fuel")

        result = json.loads(result_str)
        assert "error" in result
        assert "Ontology browse failed" in result["error"]


# ── query_knowledge_graph ──────────────────────────────────────


class TestQueryKnowledgeGraphTool:
    """Integration tests for the query_knowledge_graph MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges(self) -> None:
        """query_knowledge_graph should query KGNode/KGEdge models."""
        from nfm_db.models.kg import KGEdge, KGNode
        from nfm_mcp.server import create_mcp_server

        node_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        target_id = uuid.uuid4()

        mock_node = MagicMock(spec=KGNode)
        mock_node.id = node_id
        mock_node.label = "Uranium Dioxide"
        mock_node.node_type = "Material"
        mock_node.confidence = 0.95
        mock_node.properties = {"formula": "UO2"}
        mock_node.source_id = None
        mock_node.status = "active"

        mock_edge = MagicMock(spec=KGEdge)
        mock_edge.id = edge_id
        mock_edge.source_node_id = node_id
        mock_edge.target_node_id = target_id
        mock_edge.relation_type = "hasProperty"
        mock_edge.confidence = 0.9
        mock_edge.properties = {}

        mock_session = MagicMock(spec=AsyncSession)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        # Mock execute to return nodes then edges
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_node]

        mock_edge_result = MagicMock()
        mock_edge_result.scalars.return_value.all.return_value = [mock_edge]

        mock_session.execute = AsyncMock(
            side_effect=[mock_result, mock_edge_result],
        )

        async def _gen() -> AsyncGenerator[AsyncSession, None]:
            yield mock_session

        with patch("nfm_mcp.tools.knowledge_graph.get_db_session", _gen):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="UO2", limit=20)

        result = json.loads(result_str)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["label"] == "Uranium Dioxide"

    @pytest.mark.asyncio
    async def test_handles_db_error(self) -> None:
        """query_knowledge_graph should return JSON error on DB failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with patch(
            "nfm_mcp.tools.knowledge_graph.get_db_session",
            mock_session_gen,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            # The mock session.execute will raise AttributeError
            # which gets caught by the try/except
            result_str = await tool_fn(query="UO2")

        result = json.loads(result_str)
        assert "error" in result


# ── trigger_extraction ────────────────────────────────────────


class TestTriggerExtractionTool:
    """Integration tests for the trigger_extraction MCP tool."""

    @staticmethod
    def _make_job(job_id: str = "test-job-123") -> Any:
        """Create a mock ExtractionJob dataclass."""
        from nfm_db.services.extraction_pipeline import ExtractionJob, JobStatus

        return ExtractionJob(
            job_id=job_id,
            source_reference="/data/test.md",
            source_type="mcp_upload",
            fill_batch_id=str(uuid.uuid4()),
            status=JobStatus.QUEUED,
        )

    @pytest.mark.asyncio
    async def test_triggers_and_returns_job_id(self) -> None:
        """trigger_extraction should return job_id and queued status."""
        from nfm_mcp.server import create_mcp_server

        job = self._make_job()

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                return_value=job,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            result_str = await tool_fn(file_url="/data/test.md")

        result = json.loads(result_str)
        assert result["job_id"] == "test-job-123"
        assert result["status"] == "queued"
        assert "message" in result

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self) -> None:
        """trigger_extraction should return error for missing files."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                side_effect=FileNotFoundError(
                    "Source file not found: /missing.md",
                ),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            result_str = await tool_fn(file_url="/missing.md")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_handles_generic_error(self) -> None:
        """trigger_extraction should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Pipeline failure"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            result_str = await tool_fn(file_url="/data/test.md")

        result = json.loads(result_str)
        assert "error" in result
        assert "Extraction trigger failed" in result["error"]


# ── get_extraction_status ─────────────────────────────────────


class TestGetExtractionStatusTool:
    """Integration tests for the get_extraction_status MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_job_status(self) -> None:
        """get_extraction_status should return job details."""
        from nfm_db.services.extraction_pipeline import (
            ExtractionJob,
            JobStatus,
        )
        from nfm_mcp.server import create_mcp_server

        job = ExtractionJob(
            job_id="test-job-456",
            source_reference="/data/test.md",
            source_type="mcp_upload",
            fill_batch_id=str(uuid.uuid4()),
            status=JobStatus.COMPLETED,
        )

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            return_value=job,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="test-job-456")

        result = json.loads(result_str)
        assert result["job_id"] == "test-job-456"
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_job(self) -> None:
        """get_extraction_status should return error when job not found."""
        from nfm_mcp.server import create_mcp_server

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            return_value=None,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="nonexistent")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]
