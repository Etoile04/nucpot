"""NFM MCP Server — main entry point.

Runs a single MCP server with all tools registered:
- ``zotero_*`` tools for Zotero library read/write access
- ``nfm_*`` tools for source management and semantic search
"""

from __future__ import annotations

import logging
import os

from mcp.server import FastMCP

logger = logging.getLogger(__name__)

# Global server instance
mcp = FastMCP("nfm-mcp-server")


def register_all_tools(mcp: FastMCP) -> None:
    """Register all tool groups on the shared server instance.

    This is the single wiring point.  Each module registers its own
    prefixed tools (``zotero_``, ``nfm_``) onto the same ``mcp`` instance.
    """
    from nfm_mcp.tools.sources import register_source_tools
    from nfm_mcp.tools.zotero import register_zotero_tools
    from nfm_mcp.zotero.client import ZoteroClient

    # Initialize Zotero client from environment
    zotero_client = ZoteroClient(
        api_key=os.environ.get("ZOTERO_API_KEY", ""),
        user_id=os.environ.get("ZOTERO_USER_ID", ""),
        library_type=os.environ.get("ZOTERO_LIB_TYPE", "user"),
    )

    register_zotero_tools(mcp, zotero_client)
    register_source_tools(mcp, zotero_client)

    logger.info("All tools registered on nfm-mcp-server")


def main() -> None:
    """Entry point for the NFM MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    register_all_tools(mcp)
    mcp.run()


if __name__ == "__main__":
    main()
