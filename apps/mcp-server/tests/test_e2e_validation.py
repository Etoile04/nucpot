"""End-to-end validation of the NFM MCP server tool contracts.

NFM-1145: Validates the complete MCP server pipeline:
  1. Server creation without errors
  2. All 9 tools registered and discoverable
  3. Tool annotations (title, hints) present and correct
  4. Mock-based tools return valid data when called
  5. DB-dependent tools handle missing-database errors gracefully
  6. No unhandled exceptions in any tool response

This test exercises tool callables directly (no transport layer),
which is the appropriate level for validating tool contracts without
requiring a running PostgreSQL instance.
"""

from __future__ import annotations

import json

import pytest

from nfm_mcp.server import EXPECTED_TOOL_NAMES, create_mcp_server


# ── Fixture ───────────────────────────────────────────────────────


@pytest.fixture()
def mcp_server():
    """Create a fresh MCP server instance for each test."""
    return create_mcp_server()


@pytest.fixture()
def tool_map(mcp_server):
    """Return a name->callable mapping of all registered tools."""
    tools = mcp_server._tool_manager._tools
    return {name: tool.fn for name, tool in tools.items()}


@pytest.fixture()
def tool_meta(mcp_server):
    """Return a name->metadata mapping of all registered tools."""
    tools = mcp_server._tool_manager._tools
    return {name: tool for name, tool in tools.items()}


# ── 1. Server creation ──────────────────────────────────────────


class TestServerCreation:
    """Verify the MCP server instantiates without errors."""

    def test_create_server_returns_fastmcp(self, mcp_server):
        """Server creation returns a valid FastMCP instance with correct name."""
        assert mcp_server.name == "nfm_mcp"
        assert mcp_server.instructions is not None
        assert "Nuclear Fuel" in mcp_server.instructions

    def test_create_server_has_instructions(self, mcp_server):
        """Server instructions mention all key capabilities."""
        instructions = mcp_server.instructions
        for keyword in ("materials", "ontology", "knowledge graph", "extraction"):
            assert keyword in instructions


# ── 2. Tool registration ─────────────────────────────────────────


class TestToolRegistration:
    """Verify all 9 expected tools are registered and discoverable."""

    def test_all_expected_tools_registered(self, tool_map):
        """Every tool in EXPECTED_TOOL_NAMES is present in the server."""
        registered = set(tool_map.keys())
        expected = set(EXPECTED_TOOL_NAMES)
        assert registered == expected, (
            f"Missing: {expected - registered}, "
            f"Extra: {registered - expected}"
        )

    def test_exact_tool_count(self, tool_map):
        """Server registers exactly 9 tools."""
        assert len(tool_map) == 9

    def test_tools_are_callables(self, tool_map):
        """Every registered tool has a callable handler."""
        for name, handler in tool_map.items():
            assert callable(handler), f"Tool '{name}' is not callable"


# ── 3. Tool annotations ────────────────────────────────────────


class TestToolAnnotations:
    """Verify tools have proper MCP annotations for discoverability."""

    def _annotations_dict(self, tool) -> dict:
        """Extract annotations as a plain dict (ToolAnnotations is Pydantic)."""
        ann = tool.annotations
        if ann is None:
            return {}
        return ann.model_dump() if hasattr(ann, "model_dump") else dict(ann)

    def test_all_tools_have_title(self, tool_meta):
        """Every tool has a human-readable title annotation."""
        for name, tool in tool_meta.items():
            annotations = self._annotations_dict(tool)
            assert "title" in annotations, f"Tool '{name}' missing title"
            assert len(annotations["title"]) > 3, (
                f"Tool '{name}' title too short"
            )

    def test_read_only_tools_marked(self, tool_meta):
        """Read-only tools have readOnlyHint=true annotation."""
        read_only_tools = {
            "search_materials",
            "get_material",
            "query_properties",
            "search_sources",
            "query_potentials",
            "browse_ontology",
            "query_knowledge_graph",
            "get_extraction_status",
        }
        for name in read_only_tools:
            annotations = self._annotations_dict(tool_meta[name])
            assert annotations.get("readOnlyHint") is True, (
                f"Tool '{name}' should be marked read-only"
            )

    def test_write_tool_not_read_only(self, tool_meta):
        """trigger_extraction is correctly marked as non-read-only."""
        annotations = self._annotations_dict(tool_meta["trigger_extraction"])
        assert annotations.get("readOnlyHint") is False

    def test_all_tools_have_safety_hints(self, tool_meta):
        """Every tool has destructiveHint, idempotentHint, and openWorldHint."""
        required_hints = {"destructiveHint", "idempotentHint", "openWorldHint"}
        for name, tool in tool_meta.items():
            annotations = self._annotations_dict(tool)
            missing = required_hints - set(annotations.keys())
            assert not missing, f"Tool '{name}' missing hints: {missing}"


# ── 4. Mock-based tools return valid data ────────────────────────


class TestBrowseOntologyE2E:
    """E2E: browse_ontology returns valid ontology data."""

    @pytest.mark.asyncio
    async def test_returns_nodes_without_query(self, tool_map):
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler())
        assert isinstance(result, list)
        assert len(result) >= 12  # mock data has 12 ontology nodes

    @pytest.mark.asyncio
    async def test_returns_valid_json_structure(self, tool_map):
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler())
        for node in result:
            assert "id" in node
            assert "label" in node
            assert "entity_type" in node

    @pytest.mark.asyncio
    async def test_filter_by_material_type(self, tool_map):
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler(entity_type="material"))
        assert all(
            n.get("entity_type") == "material" for n in result
        )
        assert len(result) >= 4  # UO2, Zry4, SiC, FeCrAl

    @pytest.mark.asyncio
    async def test_search_by_query(self, tool_map):
        handler = tool_map["browse_ontology"]
        result = json.loads(await handler(query="fuel"))
        assert len(result) > 0
        assert any(
            "fuel" in str(n.get("description", "")).lower()
            or "fuel" in str(n.get("label", "")).lower()
            for n in result
        )


class TestQueryKnowledgeGraphE2E:
    """E2E: query_knowledge_graph returns valid graph data."""

    @pytest.mark.asyncio
    async def test_returns_graph_structure(self, tool_map):
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await handler(query="UO2"))
        assert "nodes" in result
        assert "edges" in result
        assert isinstance(result["nodes"], list)
        assert isinstance(result["edges"], list)

    @pytest.mark.asyncio
    async def test_nodes_have_required_fields(self, tool_map):
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await handler(query="UO2"))
        for node in result["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "entity_type" in node

    @pytest.mark.asyncio
    async def test_edges_reference_valid_nodes(self, tool_map):
        handler = tool_map["query_knowledge_graph"]
        result = json.loads(await(handler(query="material")))
        node_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert edge["source"] in node_ids or edge["target"] in node_ids


class TestSearchSourcesE2E:
    """E2E: search_sources returns valid source data."""

    @pytest.mark.asyncio
    async def test_returns_results(self, tool_map):
        handler = tool_map["search_sources"]
        result = json.loads(await handler(query="nuclear"))
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_results_have_required_fields(self, tool_map):
        handler = tool_map["search_sources"]
        result = json.loads(await handler(query="IAEA"))
        for source in result:
            assert "id" in source
            assert "authors" in source
            assert "title" in source

    @pytest.mark.asyncio
    async def test_type_filter(self, tool_map):
        handler = tool_map["search_sources"]
        result = json.loads(await handler(query="*", source_type="journal"))
        for source in result:
            assert source.get("source_type") == "journal"


class TestQueryPotentialsE2E:
    """E2E: query_potentials returns valid thermodynamic data."""

    @pytest.mark.asyncio
    async def test_returns_results_for_uo2(self, tool_map):
        handler = tool_map["query_potentials"]
        result = json.loads(await handler(material_id="UO2"))
        assert isinstance(result, list)
        assert len(result) >= 3  # FINK-LUCUTA2 has Gibbs, Cp, enthalpy

    @pytest.mark.asyncio
    async def test_results_have_required_fields(self, tool_map):
        handler = tool_map["query_potentials"]
        result = json.loads(await handler(material_id="UO2"))
        for pot in result:
            assert "id" in pot
            assert "material_id" in pot
            assert "model_name" in pot
            assert "potential_type" in pot

    @pytest.mark.asyncio
    async def test_empty_for_nonexistent(self, tool_map):
        handler = tool_map["query_potentials"]
        result = json.loads(await(handler(material_id="NONEXISTENT")))
        assert result == []


class TestExtractionToolsE2E:
    """E2E: extraction trigger and status tools work end-to-end."""

    @pytest.mark.asyncio
    async def test_trigger_returns_job_id(self, tool_map):
        handler = tool_map["trigger_extraction"]
        result = json.loads(
            await handler(file_url="https://example.com/test-paper.pdf")
        )
        assert "job_id" in result
        assert result["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_roundtrip_trigger_and_status(self, tool_map):
        trigger = tool_map["trigger_extraction"]
        status = tool_map["get_extraction_status"]

        submitted = json.loads(
            await trigger(file_url="https://example.com/e2e-test.pdf")
        )
        job_id = submitted["job_id"]

        result = json.loads(await status(job_id=job_id))
        assert result["job_id"] == job_id
        assert result["status"] == "submitted"
        assert result["source_id"] == "https://example.com/e2e-test.pdf"

    @pytest.mark.asyncio
    async def test_status_not_found_error(self, tool_map):
        handler = tool_map["get_extraction_status"]
        result = json.loads(await handler(job_id="job-nonexistent"))
        assert "error" in result
        assert "not found" in result["error"].lower()


# ── 5. DB-dependent tools: graceful error handling ──────────────


class TestDBToolErrorHandling:
    """Verify DB-dependent tools return structured errors, not exceptions."""

    @pytest.mark.asyncio
    async def test_get_material_invalid_uuid_returns_error(self, tool_map):
        """get_material returns a structured error for non-UUID input."""
        handler = tool_map["get_material"]
        result = json.loads(await handler(material_id="not-a-uuid"))
        assert "error" in result
        assert "not found" in result["error"].lower() or "valid UUID" in result["error"]

    @pytest.mark.asyncio
    async def test_query_properties_invalid_uuid_returns_error(self, tool_map):
        """query_properties returns a structured error for non-UUID input."""
        handler = tool_map["query_properties"]
        result = json.loads(await handler(material_id="not-a-uuid"))
        assert "error" in result
        assert "invalid" in result["error"].lower() or "uuid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_search_materials_returns_structured_result(self, tool_map):
        """search_materials returns a result (data or error), never crashes."""
        handler = tool_map["search_materials"]
        result_str = await handler(query="UO2")
        result = json.loads(result_str)
        assert "error" in result or "items" in result or isinstance(result, list)


# ── 6. No unhandled exceptions ───────────────────────────────────


class TestNoUnhandledExceptions:
    """Every tool returns valid JSON, never raises an unhandled exception."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,kwargs", [
        ("search_materials", {"query": "UO2"}),
        ("get_material", {"material_id": "not-a-uuid"}),
        ("query_properties", {"material_id": "not-a-uuid"}),
        ("search_sources", {"query": "Finkelstein"}),
        ("query_potentials", {"material_id": "UO2"}),
        ("browse_ontology", {}),
        ("query_knowledge_graph", {"query": "UO2"}),
        ("trigger_extraction", {"file_url": "https://example.com/test.pdf"}),
        ("get_extraction_status", {"job_id": "job-nonexistent"}),
    ])
    async def test_tool_returns_valid_json(self, tool_map, tool_name, kwargs):
        """Every tool returns parseable JSON without crashing."""
        handler = tool_map[tool_name]
        result_str = await handler(**kwargs)
        parsed = json.loads(result_str)
        assert isinstance(parsed, (dict, list)), (
            f"Tool '{tool_name}' returned unexpected type: {type(parsed)}"
        )
