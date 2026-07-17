"""Zotero MCP tool registration.

Registers 9 ``zotero_``-prefixed tools on the shared MCPServer instance.
Vendored from SMABoundless/zotero-mcp-server (MIT license), adapted for
the NFM MCP server's single-server architecture.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import FastMCP
from mcp.types import ToolAnnotations

from nfm_mcp.zotero.client import ZoteroClient, format_item

logger = logging.getLogger(__name__)


def register_zotero_tools(
    mcp: FastMCP,
    client: ZoteroClient,
) -> None:
    """Register all 9 Zotero tools on the shared MCP server.

    Args:
        mcp: The shared FastMCP instance.
        client: An initialized ZoteroClient.
    """

    @mcp.tool(
        name="zotero_search_library",
        description=(
            "Search your Zotero library by any keyword, title, author, or phrase. "
            "Returns matching items with key metadata."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def zotero_search_library(
        query: str,
        limit: int = 25,
    ) -> str:
        """Search the Zotero library."""
        try:
            items = client.search_items(query, limit=limit)
            if not items:
                return f"No results found for: {query}"
            return (
                f"Found {len(items)} item(s):\n\n"
                + "\n\n".join(format_item(i) for i in items)
            )
        except Exception as exc:
            logger.exception("zotero_search_library failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_get_collections",
        description=(
            "List all collections (folders) in your Zotero library, "
            "with their keys and item counts."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def zotero_get_collections() -> str:
        """List all Zotero collections."""
        try:
            cols = client.get_collections()
            if not cols:
                return "Your library has no collections yet."
            lines = [
                f"[{c['key']}] {c['data'].get('name', '?')}  "
                f"({c['data'].get('numItems', 0)} items)"
                for c in cols
            ]
            return "Your Zotero collections:\n\n" + "\n".join(lines)
        except Exception as exc:
            logger.exception("zotero_get_collections failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_get_collection_items",
        description="Get all items inside a specific Zotero collection.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def zotero_get_collection_items(
        collection_key: str,
        limit: int = 50,
    ) -> str:
        """Get items in a collection."""
        try:
            items = client.get_collection_items(collection_key, limit=limit)
            if not items:
                return "No items found in this collection."
            return (
                f"{len(items)} item(s):\n\n"
                + "\n\n".join(format_item(i) for i in items)
            )
        except Exception as exc:
            logger.exception("zotero_get_collection_items failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_get_item_details",
        description="Get the full metadata record for a specific Zotero item by its key.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def zotero_get_item_details(item_key: str) -> str:
        """Get full item metadata."""
        try:
            item = client.get_item(item_key)
            return json.dumps(item.get("data", {}), indent=2)
        except Exception as exc:
            logger.exception("zotero_get_item_details failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_get_recent_items",
        description="Get the most recently added items in your Zotero library.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def zotero_get_recent_items(limit: int = 10) -> str:
        """Get recently added items."""
        try:
            items = client.get_recent_items(limit=limit)
            if not items:
                return "No items found."
            return (
                f"Most recent {len(items)} item(s):\n\n"
                + "\n\n".join(format_item(i) for i in items)
            )
        except Exception as exc:
            logger.exception("zotero_get_recent_items failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_add_article",
        description=(
            "Add a single journal article to your Zotero library. "
            "Use this after finding a paper via PubMed, Scopus, CrossRef, or ERIC."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def zotero_add_article(
        title: str,
        authors: list[dict[str, str]] | None = None,
        journal: str | None = None,
        year: str | None = None,
        doi: str | None = None,
        abstract: str | None = None,
        volume: str | None = None,
        issue: str | None = None,
        pages: str | None = None,
        url: str | None = None,
        pmid: str | None = None,
        issn: str | None = None,
        collection_key: str | None = None,
    ) -> str:
        """Add a journal article to Zotero."""
        try:
            meta: dict[str, Any] = {
                "title": title,
                "authors": authors or [],
                "journal": journal,
                "year": year,
                "doi": doi,
                "abstract": abstract,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "url": url,
                "pmid": pmid,
                "issn": issn,
            }
            resp = client.add_article(meta, collection_key=collection_key)
            if resp and resp.get("successful"):
                key = list(resp["successful"].values())[0].get("key", "?")
                return f"Added to Zotero: \"{title}\"\n  Item key: {key}"
            return f"Failed to add item.\nResponse: {json.dumps(resp)}"
        except Exception as exc:
            logger.exception("zotero_add_article failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_add_multiple_articles",
        description=(
            "Add a batch of journal articles to Zotero at once. "
            "Ideal for saving a set of search results in one step."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def zotero_add_multiple_articles(
        articles: list[dict[str, Any]],
        collection_key: str | None = None,
    ) -> str:
        """Batch-add articles to Zotero."""
        try:
            resp = client.add_multiple_articles(articles, collection_key)
            n_added = len(resp.get("successful", {}))
            n_failed = len(resp.get("failed", {}))
            preview = "; ".join(a.get("title", "?") for a in articles[:4])
            if len(articles) > 4:
                preview += f" ... and {len(articles) - 4} more"
            msg = (
                f"Added {n_added} of {len(articles)} article(s) to Zotero.\n"
                f"  {preview}"
            )
            if n_failed:
                msg += f"\n  Failed: {n_failed}"
            if collection_key:
                msg += f"\n  Saved to collection: {collection_key}"
            return msg
        except Exception as exc:
            logger.exception("zotero_add_multiple_articles failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_create_collection",
        description="Create a new collection (folder) in your Zotero library.",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def zotero_create_collection(
        name: str,
        parent_key: str | None = None,
    ) -> str:
        """Create a Zotero collection."""
        try:
            resp = client.create_collection(name, parent_key=parent_key)
            if resp and resp.get("successful"):
                key = list(resp["successful"].values())[0].get("key", "?")
                return f"Collection \"{name}\" created.\n  Collection key: {key}"
            return f"Failed to create collection.\nResponse: {json.dumps(resp)}"
        except Exception as exc:
            logger.exception("zotero_create_collection failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="zotero_add_item_to_collection",
        description="Add an existing Zotero item to a collection.",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def zotero_add_item_to_collection(
        item_key: str,
        collection_key: str,
    ) -> str:
        """Add an item to a collection."""
        try:
            client.add_item_to_collection(item_key, collection_key)
            return f"Item {item_key} added to collection {collection_key}"
        except Exception as exc:
            logger.exception("zotero_add_item_to_collection failed")
            return f"Error: {exc}"
