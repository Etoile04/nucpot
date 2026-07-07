"""NFM source management MCP tools.

Registers ``nfm_``-prefixed tools on the shared MCPServer:
- ``nfm_search_sources`` — Semantic search via ChromaDB
- ``nfm_import_from_zotero`` — Zotero item → NFM DataSource + Author
- ``nfm_batch_import_from_zotero`` — Bulk import from collection
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import FastMCP
from mcp.types import ToolAnnotations

from nfm_mcp.embeddings import semantic_search
from nfm_mcp.zotero.client import ZoteroClient, format_item

logger = logging.getLogger(__name__)


def _extract_creators(item: dict[str, Any]) -> list[dict[str, str]]:
    """Extract author dicts from a Zotero item.

    Returns a list of ``{firstName, lastName}`` dicts.
    """
    data = item.get("data", item)
    return [
        {
            "firstName": c.get("firstName", ""),
            "lastName": c.get("lastName", ""),
        }
        for c in data.get("creators", [])
        if c.get("creatorType") == "author"
    ]


def _item_to_source_data(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a Zotero item to a DataSource-compatible dict.

    Returns a new dict with fields matching the DataSource model.
    """
    data = item.get("data", item)
    year_str = (data.get("date") or "")[:4]
    return {
        "external_key": item.get("key", ""),
        "title": data.get("title", ""),
        "doi": data.get("DOI", ""),
        "journal": data.get("publicationTitle", ""),
        "year": int(year_str) if year_str.isdigit() else None,
        "volume": data.get("volume"),
        "pages": data.get("pages"),
        "source_type": data.get("itemType", "journalArticle"),
        "abstract": data.get("abstractNote", ""),
        "external_url": data.get("url", ""),
        "authors": _extract_creators(item),
    }


def register_source_tools(
    mcp: FastMCP,
    zotero_client: ZoteroClient,
) -> None:
    """Register NFM source management tools on the shared MCP server.

    Args:
        mcp: The shared FastMCP instance.
        zotero_client: An initialized ZoteroClient.
    """

    @mcp.tool(
        name="nfm_search_sources",
        description=(
            "Search NFM data sources using semantic similarity. "
            "Returns the most relevant sources ranked by similarity to the query."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def nfm_search_sources(
        query: str,
        source_type: str | None = None,
        year_range: str | None = None,
        top_k: int = 10,
    ) -> str:
        """Semantic search over NFM data sources."""
        try:
            results = semantic_search(
                query=query,
                top_k=top_k,
                source_type=source_type,
                year_range=year_range,
            )
            if not results:
                return f"No sources found for: {query}"
            lines = []
            for r in results:
                meta = r.get("metadata", {})
                lines.append(
                    f"[{meta.get('id', '?')}] {meta.get('title', 'No title')}\n"
                    f"  Journal : {meta.get('journal', 'N/A')}\n"
                    f"  Year    : {meta.get('year', 'N/A')}\n"
                    f"  DOI     : {meta.get('doi', 'N/A')}\n"
                    f"  Score   : {r.get('distance', 0):.4f}"
                )
            return f"Found {len(results)} source(s):\n\n" + "\n\n".join(lines)
        except Exception as exc:
            logger.exception("nfm_search_sources failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="nfm_import_from_zotero",
        description=(
            "Import a single Zotero item into the NFM database as a DataSource "
            "with associated Author records."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def nfm_import_from_zotero(zotero_key: str) -> str:
        """Import a Zotero item into NFM."""
        try:
            item = zotero_client.get_item(zotero_key)
            source_data = _item_to_source_data(item)

            # TODO: Write to database via SQLAlchemy async session
            # This requires the nfm_db engine to be initialized.
            # For now, return the structured data that would be written.
            return (
                f"Prepared for import:\n"
                f"  Zotero Key : {source_data['external_key']}\n"
                f"  Title      : {source_data['title']}\n"
                f"  DOI        : {source_data['doi'] or 'N/A'}\n"
                f"  Journal    : {source_data['journal'] or 'N/A'}\n"
                f"  Year       : {source_data['year'] or 'N/A'}\n"
                f"  Authors    : {len(source_data['authors'])}\n"
                f"\nSource data (JSON):\n{json.dumps(source_data, indent=2, default=str)}"
            )
        except Exception as exc:
            logger.exception("nfm_import_from_zotero failed")
            return f"Error: {exc}"

    @mcp.tool(
        name="nfm_batch_import_from_zotero",
        description=(
            "Bulk import all items from a Zotero collection into the NFM "
            "database as DataSource + Author records."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def nfm_batch_import_from_zotero(
        collection_key: str | None = None,
        limit: int = 50,
    ) -> str:
        """Batch import from Zotero collection."""
        try:
            if collection_key:
                items = zotero_client.get_collection_items(collection_key, limit=limit)
            else:
                items = zotero_client.get_recent_items(limit=limit)

            if not items:
                return "No items found to import."

            sources = [_item_to_source_data(i) for i in items]
            total_authors = sum(len(s["authors"]) for s in sources)

            # TODO: Write to database via SQLAlchemy async session
            return (
                f"Prepared {len(sources)} source(s) for import "
                f"({total_authors} total authors):\n\n"
                + "\n".join(
                    f"  [{s['external_key']}] {s['title'][:80]}"
                    for s in sources
                )
            )
        except Exception as exc:
            logger.exception("nfm_batch_import_from_zotero failed")
            return f"Error: {exc}"
