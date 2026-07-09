"""Tests for MCP tool handlers and helper functions.

Exercises mock-data-based tools directly via the registered MCP
tool callables, and tests pure helper functions in isolation.
"""

from __future__ import annotations

import json

import pytest

from nfm_mcp.server import create_mcp_server
from nfm_mcp.tools.extraction import (
    GetExtractionStatusInput,
    TriggerExtractionInput,
)
from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput
from nfm_mcp.tools.mock_data import EXTRACTION_JOBS, generate_job_id
from nfm_mcp.tools.ontology import BrowseOntologyInput
from nfm_mcp.tools.potentials import QueryPotentialsInput
from nfm_mcp.tools.sources import SearchSourcesInput


# ── Fixture: create server once and expose tool callables ────────


@pytest.fixture()
def tool_map():
    """Build an MCP server and return a name→callable map of tool handlers."""
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
        params = TriggerExtractionInput(file_url="https://example.com/test.pdf")
        result = json.loads(await handler(params))
        assert "job_id" in result
        assert result["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_trigger_extraction_stores_job(self, tool_map: dict) -> None:
        handler = tool_map["trigger_extraction"]
        params = TriggerExtractionInput(file_url="https://example.com/test.pdf")
        result = json.loads(await handler(params))
        job_id = result["job_id"]
        assert job_id in EXTRACTION_JOBS
        assert EXTRACTION_JOBS[job_id]["source_id"] == "https://example.com/test.pdf"

    @pytest.mark.asyncio
    async def test_get_extraction_status_existing(self, tool_map: dict) -> None:
        existing_id = list(EXTRACTION_JOBS.keys())[0]
        handler = tool_map["get_extraction_status"]
        params = GetExtractionStatusInput(job_id=existing_id)
        result = json.loads(await handler(params))
        assert result["job_id"] == existing_id

    @pytest.mark.asyncio
    async def test_get_extraction_status_not_found(self, tool_map: dict) -> None:
        handler = tool_map["get_extraction_status"]
        params = GetExtractionStatusInput(job_id="job-nonexistent")
        result = json.loads(await handler(params))
        assert "error" in result


# ── Knowledge graph tools ───────────────────────────────────────


class TestKnowledgeGraphTools:
    """Tests for query_knowledge_graph tool."""

    @pytest.mark.asyncio
    async def test_query_kg_returns_nodes(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        params = QueryKnowledgeGraphInput(query="UO2")
        result = json.loads(await handler(params))
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) > 0

    @pytest.mark.asyncio
    async def test_query_kg_entity_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        params = QueryKnowledgeGraphInput(
            query="material", entity_types=["material"],
        )
        result = json.loads(await handler(params))
        for node in result["nodes"]:
            assert str(node.get("entity_type", "")).lower() == "material"

    @pytest.mark.asyncio
    async def test_query_kg_limit(self, tool_map: dict) -> None:
        handler = tool_map["query_knowledge_graph"]
        params = QueryKnowledgeGraphInput(query="material", limit=1)
        result = json.loads(await handler(params))
        assert len(result["nodes"]) <= 1


# ── Ontology tools ──────────────────────────────────────────────


class TestOntologyTools:
    """Tests for browse_ontology tool."""

    @pytest.mark.asyncio
    async def test_browse_ontology_returns_nodes(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        params = BrowseOntologyInput()
        result = json.loads(await handler(params))
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_browse_ontology_query_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        params = BrowseOntologyInput(query="reactor")
        result = json.loads(await handler(params))
        assert len(result) > 0
        # "reactor" appears in description fields of ontology nodes
        assert any(
            "reactor" in str(n.get("description", "")).lower()
            for n in result
        )

    @pytest.mark.asyncio
    async def test_browse_ontology_entity_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        params = BrowseOntologyInput(entity_type="material")
        result = json.loads(await handler(params))
        assert all(
            str(n.get("entity_type", "")) == "material" for n in result
        )

    @pytest.mark.asyncio
    async def test_browse_ontology_parent_filter(self, tool_map: dict) -> None:
        handler = tool_map["browse_ontology"]
        params = BrowseOntologyInput(parent_id="onto-root")
        result = json.loads(await handler(params))
        assert all(n.get("parent_id") == "onto-root" for n in result)


# ── Potential tools ──────────────────────────────────────────────


class TestPotentialTools:
    """Tests for query_potentials tool."""

    @pytest.mark.asyncio
    async def test_query_potentials_by_material(self, tool_map: dict) -> None:
        handler = tool_map["query_potentials"]
        params = QueryPotentialsInput(material_id="UO2")
        result = json.loads(await handler(params))
        assert isinstance(result, list)
        assert all(p.get("material_id") == "UO2" for p in result)

    @pytest.mark.asyncio
    async def test_query_potentials_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["query_potentials"]
        params = QueryPotentialsInput(material_id="UO2", potential_type="Gibbs")
        result = json.loads(await handler(params))
        assert all(
            "gibbs" in str(p.get("potential_type", "")).lower()
            for p in result
        )

    @pytest.mark.asyncio
    async def test_query_potentials_model_filter(self, tool_map: dict) -> None:
        handler = tool_map["query_potentials"]
        params = QueryPotentialsInput(material_id="UO2", model_name="FINK")
        result = json.loads(await handler(params))
        assert all(
            "fink" in str(p.get("model_name", "")).lower() for p in result
        )

    @pytest.mark.asyncio
    async def test_query_potentials_temp_range(self, tool_map: dict) -> None:
        handler = tool_map["query_potentials"]
        params = QueryPotentialsInput(
            material_id="UO2", temperature_range="300-3000 K",
        )
        result = json.loads(await handler(params))
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_potentials_no_match(self, tool_map: dict) -> None:
        handler = tool_map["query_potentials"]
        params = QueryPotentialsInput(material_id="NONEXISTENT")
        result = json.loads(await handler(params))
        assert result == []


# ── Source tools ────────────────────────────────────────────────


class TestSourceTools:
    """Tests for search_sources tool."""

    @pytest.mark.asyncio
    async def test_search_sources_returns_results(self, tool_map: dict) -> None:
        handler = tool_map["search_sources"]
        params = SearchSourcesInput(query="Finkelstein")
        result = json.loads(await handler(params))
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_search_sources_type_filter(self, tool_map: dict) -> None:
        handler = tool_map["search_sources"]
        params = SearchSourcesInput(query="nuclear", source_type="journal")
        result = json.loads(await handler(params))
        assert all(
            str(s.get("source_type", "")) == "journal" for s in result
        )

    @pytest.mark.asyncio
    async def test_search_sources_pagination(self, tool_map: dict) -> None:
        handler = tool_map["search_sources"]
        params = SearchSourcesInput(query="nuclear", limit=1, offset=0)
        result = json.loads(await handler(params))
        assert len(result) <= 1

    @pytest.mark.asyncio
    async def test_search_sources_no_match(self, tool_map: dict) -> None:
        handler = tool_map["search_sources"]
        params = SearchSourcesInput(query="XYZNONEXISTENT")
        result = json.loads(await handler(params))
        assert result == []


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


# ── Source helper functions ─────────────────────────────────────


class TestSourceHelpers:
    """Tests for pure helper functions in sources module."""

    def test_matches_query_authors(self) -> None:
        from nfm_mcp.tools.sources import _matches_query

        source = {
            "authors": "J.K. Finkelstein",
            "title": "Thermal conductivity of UO2",
            "journal": "Journal of Nuclear Materials",
        }
        assert _matches_query(source, "finkelstein") is True
        assert _matches_query(source, "nonexistent") is False

    def test_matches_query_title(self) -> None:
        from nfm_mcp.tools.sources import _matches_query

        source = {
            "authors": "IAEA",
            "title": "Thermophysical Properties Database",
            "journal": "IAEA-TECDOC",
        }
        assert _matches_query(source, "thermophysical") is True

    def test_matches_query_doi(self) -> None:
        from nfm_mcp.tools.sources import _matches_query

        source = {
            "authors": "J.K. Finkelstein",
            "title": "Test",
            "journal": "Test Journal",
            "doi": "10.1016/S0022-3115",
        }
        assert _matches_query(source, "10.1016") is True

    def test_matches_query_case_insensitive(self) -> None:
        from nfm_mcp.tools.sources import _matches_query

        source = {"authors": "IAEA", "title": "Database", "journal": ""}
        assert _matches_query(source, "IAEA") is True
        assert _matches_query(source, "iaea") is True


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
