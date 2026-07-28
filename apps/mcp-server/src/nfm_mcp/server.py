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
# Note: the 9 zotero_* tools are only registered when Zotero credentials
# are configured (see _register_zotero_if_configured).  Tests that need
# them in isolation call register_zotero_tools directly.
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


def _register_zotero_if_configured(mcp: FastMCP, settings: object) -> None:
    """Register the 9 Zotero tools when credentials are configured.

    Imports are deferred to keep ``pyzotero`` optional -- the MCP server
    can still start (and pass tests) without the Zotero dependency.  If
    the credentials are missing, or ``pyzotero`` is not installed, the
    tools are simply skipped and a debug log line is emitted.
    """
    api_key = getattr(settings, "zotero_api_key", "")
    user_id = getattr(settings, "zotero_user_id", "")
    if not api_key or not user_id:
        logger.debug(
            "Zotero credentials not configured (set NFM_MCP_ZOTERO_API_KEY "
            "and NFM_MCP_ZOTERO_USER_ID); skipping zotero_* tool registration"
        )
        return
    try:
        from nfm_mcp.tools.zotero import register_zotero_tools
        from nfm_mcp.zotero.client import ZoteroClient
    except ImportError:
        logger.warning(
            "Zotero credentials present but pyzotero is not installed; "
            "pip install pyzotero to enable zotero_* tools"
        )
        return
    try:
        client = ZoteroClient(
            api_key=api_key,
            user_id=user_id,
            library_type=getattr(settings, "zotero_library_type", "user"),
        )
        register_zotero_tools(mcp, client)
        logger.info("Registered 9 zotero_* MCP tools")
    except Exception:
        logger.exception("Failed to register Zotero tools")


def create_mcp_server() -> FastMCP:
    """Build and return the NFM MCP server with all tools registered.

    This function is transport-independent -- it does NOT start any
    transport (stdio, HTTP, SSE). The caller decides how to run it.
    """
    settings = get_settings()

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
    # Zotero tools are only registered when configured (NFM-829).
    _register_zotero_if_configured(mcp, settings)

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
