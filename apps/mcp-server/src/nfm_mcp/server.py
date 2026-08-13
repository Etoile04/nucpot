"""NFM MCP Server — transport-independent core.

Creates the FastMCP instance, registers all tools, and delegates
transport selection to the CLI entry-point.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from nfm_mcp.deps import get_settings
from nfm_mcp.tools.extraction import register_extraction_tools
from nfm_mcp.tools.knowledge_graph import register_kg_tools
from nfm_mcp.tools.materials import register_material_tools
from nfm_mcp.tools.ontology import register_ontology_tools
from nfm_mcp.tools.potentials import register_potential_tools
from nfm_mcp.tools.properties import register_property_tools
from nfm_mcp.tools.sources import register_source_tools

logger = logging.getLogger(__name__)

# ── Expected tool names (used in tests) ──────────────────────
EXPECTED_TOOL_NAMES: list[str] = [
    "search_materials",
    "get_material",
    "query_properties",
    "search_sources",
    "query_potentials",
    "browse_ontology",
    "query_knowledge_graph",
    "trigger_extraction",
    "get_extraction_status",
]


def create_mcp_server() -> FastMCP:
    """Build and return the NFM MCP server with all tools registered.

    This function is transport-independent -- it does NOT start any
    transport (stdio, HTTP, SSE). The caller decides how to run it.
    """
    mcp = FastMCP(
        "nfm_mcp",
        instructions=(
            "Nuclear Fuel & Materials Properties Database (NFM) MCP Server.\n"
            "Provides tools to search nuclear materials, query property data,\n"
            "browse the domain ontology, query the knowledge graph, and\n"
            "trigger document extraction pipelines."
        ),
    )

    # Register all tool groups
    register_material_tools(mcp)
    register_property_tools(mcp)
    register_source_tools(mcp)
    register_potential_tools(mcp)
    register_ontology_tools(mcp)
    register_kg_tools(mcp)
    register_extraction_tools(mcp)

    return mcp


def main() -> None:
    """CLI entry-point -- starts the server with the configured transport.

    The host/port settings are passed to the MCP runtime via environment
    variables (``MCP_SERVER_HOST`` / ``MCP_SERVER_PORT``) since newer
    versions of ``FastMCP.run()`` no longer accept them as keyword
    arguments.
    """
    import os

    settings = get_settings()
    mcp = create_mcp_server()

    # Newer FastMCP.run() reads host/port from its own settings layer
    # rather than kwargs.  Propagate our NFM_MCP_* settings there.
    os.environ.setdefault("MCP_SERVER_HOST", settings.host)
    os.environ.setdefault("MCP_SERVER_PORT", str(settings.port))

    transport = settings.transport.lower()
    if transport == "stdio":
        mcp.run()
    elif transport in ("streamable_http", "streamable-http", "http"):
        mcp.run(transport="streamable-http")
    elif transport == "sse":
        mcp.run(transport="sse")
    else:
        msg = (
            f"Unknown transport: {transport!r}. "
            "Use 'stdio', 'streamable_http', or 'sse'."
        )
        raise ValueError(msg)
