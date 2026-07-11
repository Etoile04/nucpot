"""Tests for MCP tool handlers and helper functions.

Exercises mock-data-based tools directly via the registered MCP
tool callables, and tests pure helper functions in isolation.
Service-backed tools (sources, potentials, materials) are tested
with mocked DB sessions.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_mcp.server import create_mcp_server
from nfm_mcp.tools.mock_data import EXTRACTION_JOBS, generate_job_id


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


def _empty_source_list():
    """Return an empty PaginatedResponse for sources."""
    from nfm_db.schemas.common import PaginatedResponse

    return PaginatedResponse(items=[], total=0, page=1, limit=20, pages=0)


def _empty_potential_list():
    """Return an empty PotentialListResponse."""
    from nfm_db.schemas.potential import PotentialListResponse

    return PotentialListResponse(
        potentials=[], total=0, page=1, limit=100, total_pages=0,
    )


# ── Fixture: create server once and expose tool callables ────────


@pytest.fixture()
def tool_map():
    """Build an MCP server and return a name->callable map of tool handlers."""
    mcp = create_mcp_server()
    tools = mcp._tool_manager._tools
    return {
        name: tool.fn
        for name, tool in tools.items()
    }


# ── Mock data helpers ──────────────────────────────────────────


class TestMockData:
    """Tests for mock_data module."""

    def test_generate_job_id_is_unique(self) -> None:
        ids = {generate_job_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generate_job_id_format(self) -> None:
        job_id = generate_job_id()
        assert job_id.startswith("job-")
        assert len(job_id) == 12  # "job-" + 8 hex chars

    def test_extraction_jobs_has_mock_data(self) -> None:
        assert len(EXTRACTION_JOBS) >= 2
        for job_id, job in EXTRACTION_JOBS.items():
            assert job_id.startswith("job-")
            assert "status" in job


# ── Extraction tools ─────────────────────────────────────────────


class TestExtractionTools:
    """Tests for trigger_extraction and get_extraction_status tools."""

    @pytest.mark.asyncio
    async def test_trigger_extraction_returns_job_id(self, tool_map: dict) -> None:
        handler = tool_map["trigger_extraction"]
        result = json.loads(await handler(file_url="https://example.com/test.pdf"))
        assert "job_id" in result
        assert result["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_trigger_extraction_stores_job(self, tool_map: dict) -> None:
        handler = tool_map["trigger_extraction"]
        result = json.loads(await handler(file_url="https://example.com/test.pdf"))
        job_id = result["job_id"]
        assert job_id in EXTRACTION_JOBS
        assert EXTRACTION_JOBS[job_id]["source_id"] == "https://example.com/test.pdf"

    @pytest.mark.asyncio
    async def test_get_extraction_status_existing(self, tool_map: dict) -> None:
        existing_id = list(EXTRACTION_JOBS.keys())[0]
        handler = tool_map["get_extraction_status"]
        result = json.loads(await handler(job_id=existing_id))
        assert result["job_id"] == existing_id

    @pytest.mark.asyncio
    async def test_get_extraction_status_not_found(self, tool_map: dict) -> None:
        handler = tool_map["get_extraction_status"]
        result = json.loads(await handler(job_id="job-nonexistent"))
        assert "error" in result


# ── Knowledge graph tools ───────────────────────────────────────


class TestKnowledgeGraphTools:
    """Tests for query_knowledge_graph tool."""

    @pytest.mark.asyncio
    async def test_query_kg_returns_nodes(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await handler(query="UO2"))
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) > 0

    @pytest.mark.asyncio
    async def test_query_kg_entity_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await handler(query="material", entity_types=["material"]))
        for node in result["nodes"]:
            assert str(node.get("entity_type", "")).lower() == "material"

    @pytest.mark.asyncio
    async def test_query_kg_limit(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await handler(query="material", limit=1))
        assert len(result["nodes"]) <= 1


# ── Ontology tools ──────────────────────────────────────────────


class TestOntologyTools:
    """Tests for browse_ontology tool."""

    @pytest.mark.asyncio
    async def test_browse_ontology_returns_nodes(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler())
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_browse_ontology_query_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler(query="reactor"))
        assert len(result) > 0
        # "reactor" appears in description fields of ontology nodes
        assert any(
            "reactor" in str(n.get("description", "")).lower()
            for n in result
        )

    @pytest.mark.asyncio
    async def test_browse_ontology_entity_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler(entity_type="material"))
        assert all(
            str(n.get("entity_type", "")) == "material" for n in result
        )

    @pytest.mark.asyncio
    async def test_browse_ontology_parent_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler(parent_id="onto-root"))
        assert all(n.get("parent_id") == "onto-root" for n in result)


# ── Potential tools (Phase B -- service-backed) ─────────────────


class TestPotentialTools:
    """Tests for query_potentials and get_potential tools (service-backed)."""

    @pytest.mark.asyncio
    async def test_query_potentials_service_call(self, tool_map: dict) -> None:
        """query_potentials delegates to list_potentials service."""
        handler = tool_map["query_potentials"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=_empty_potential_list(),
            ),
        ):
            result = json.loads(await handler(material_id="UO2"))
        assert "potentials" in result
        assert "total" in result

    @pytest.mark.asyncio
    async def test_query_potentials_error_returns_json(self, tool_map: dict) -> None:
        """query_potentials returns JSON error on service failure."""
        handler = tool_map["query_potentials"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
        ):
            result = json.loads(await handler(material_id="UO2"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_potential_invalid_uuid(self, tool_map: dict) -> None:
        """get_potential returns error for non-UUID."""
        handler = tool_map["get_potential"]
        result = json.loads(await handler(potential_id="bad"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_potential_service_call(self, tool_map: dict) -> None:
        """get_potential delegates to get_potential_by_id service."""
        pid = str(uuid.uuid4())
        handler = tool_map["get_potential"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_gen),
            patch(
                "nfm_db.services.potential_service.get_potential_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = json.loads(await handler(potential_id=pid))
        assert "error" in result
        assert "not found" in result["error"]


# ── Source tools (Phase B -- service-backed) ─────────────────────


class TestSourceTools:
    """Tests for search_sources and get_source tools (service-backed)."""

    @pytest.mark.asyncio
    async def test_search_sources_service_call(self, tool_map: dict) -> None:
        """search_sources delegates to list_sources service."""
        handler = tool_map["search_sources"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=_empty_source_list(),
            ),
        ):
            result = json.loads(await handler(query="test"))
        assert "items" in result
        assert "total" in result

    @pytest.mark.asyncio
    async def test_search_sources_error_returns_json(self, tool_map: dict) -> None:
        """search_sources returns JSON error on service failure."""
        handler = tool_map["search_sources"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
        ):
            result = json.loads(await handler(query="test"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_source_invalid_uuid(self, tool_map: dict) -> None:
        """get_source returns error for non-UUID."""
        handler = tool_map["get_source"]
        result = json.loads(await handler(source_id="bad"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_source_service_call(self, tool_map: dict) -> None:
        """get_source delegates to get_source service."""
        sid = str(uuid.uuid4())
        handler = tool_map["get_source"]
        mock_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_gen),
            patch(
                "nfm_db.services.source_service.get_source",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = json.loads(await handler(source_id=sid))
        assert "error" in result
        assert "not found" in result["error"]


# ── Potential helper functions (imported directly) ───────────────


class TestPotentialHelpers:
    """Tests for pure helper functions in potentials module."""

    def test_parse_temperature_range_valid(self) -> None:
        from nfm_mcp.tools.potentials import _parse_temperature_range

        assert _parse_temperature_range("300-3000 K") == (300.0, 3000.0)
        assert _parse_temperature_range("298.15-3138k") == (298.15, 3138.0)

    def test_parse_temperature_range_invalid(self) -> None:
        from nfm_mcp.tools.potentials import _parse_temperature_range

        assert _parse_temperature_range("not-a-range") is None
        assert _parse_temperature_range("300") is None
        assert _parse_temperature_range("abc-xyz K") is None

    def test_ranges_overlap(self) -> None:
        from nfm_mcp.tools.potentials import _ranges_overlap

        assert _ranges_overlap([300, 1500], (200, 400)) is True
        assert _ranges_overlap([300, 1500], (2000, 3000)) is False
        assert _ranges_overlap([300, 1500], (300, 1500)) is True

    def test_ranges_overlap_short(self) -> None:
        from nfm_mcp.tools.potentials import _ranges_overlap

        assert _ranges_overlap([300], (200, 400)) is False


# ── Knowledge graph helper functions ────────────────────────────


class TestKGHelpers:
    """Tests for pure helper functions in knowledge_graph module."""

    def test_query_matches_label(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _query_matches

        node = {"id": "kg-UO2", "label": "UO2", "entity_type": "material"}
        assert _query_matches(node, "uo2") is True
        assert _query_matches(node, "SiC") is False

    def test_query_matches_entity_type(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _query_matches

        node = {"id": "kg-UO2", "label": "UO2", "entity_type": "material"}
        assert _query_matches(node, "material") is True

    def test_query_matches_case_insensitive(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _query_matches

        node = {"id": "kg-UO2", "label": "UO2", "entity_type": "material"}
        assert _query_matches(node, "UO2") is True
        assert _query_matches(node, "uo2") is True


# ── Ontology helper functions ───────────────────────────────────


class TestOntologyHelpers:
    """Tests for pure helper functions in ontology module."""

    def test_matches_query_label(self) -> None:
        from nfm_mcp.tools.ontology import _matches_query

        node = {
            "id": "onto-uo2",
            "label": "UO2 (Uranium Dioxide)",
            "description": "Primary ceramic nuclear fuel",
        }
        assert _matches_query(node, "uranium") is True
        assert _matches_query(node, "SiC") is False

    def test_matches_query_description(self) -> None:
        from nfm_mcp.tools.ontology import _matches_query

        node = {
            "id": "onto-fuel",
            "label": "Fuel Materials",
            "description": "Materials used as nuclear reactor fuel",
        }
        assert _matches_query(node, "reactor") is True

    def test_matches_query_id(self) -> None:
        from nfm_mcp.tools.ontology import _matches_query

        node = {"id": "onto-uo2", "label": "UO2", "description": "fuel"}
        assert _matches_query(node, "onto-uo2") is True

    def test_matches_query_case_insensitive(self) -> None:
        from nfm_mcp.tools.ontology import _matches_query

        node = {
            "id": "onto-fuel",
            "label": "Fuel Materials",
            "description": "Materials used as nuclear reactor fuel",
        }
        assert _matches_query(node, "FUEL") is True


# ── Server main() ────────────────────────────────────────────


class TestServerMain:
    """Tests for the server main() function error paths."""

    def test_main_unknown_transport_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_mcp.server import main

        monkeypatch.setenv("NFM_MCP_TRANSPORT", "websocket")
        monkeypatch.setenv("NFM_MCP_PORT", "9999")
        with pytest.raises(ValueError, match="Unknown transport"):
            main()

    def test_material_type_to_category_id(self) -> None:
        from nfm_mcp.tools.materials import _material_type_to_category_id

        assert _material_type_to_category_id("fuel") is None
        assert _material_type_to_category_id("cladding") is None
