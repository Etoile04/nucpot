"""Literature source search tools."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.tools.mock_data import SOURCES


class SearchSourcesInput(BaseModel):
    """Input for searching literature sources."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Search query for literature sources (e.g., 'FinkLucuta2000')",
        min_length=1,
        max_length=500,
    )
    source_type: Optional[str] = Field(
        default=None,
        description="Filter by source type (e.g., 'journal', 'report', 'handbook')",
    )
    limit: int = Field(
        default=20,
        description="Maximum results to return (1-100)",
        ge=1,
        le=100,
    )
    offset: int = Field(
        default=0,
        description="Pagination offset",
        ge=0,
    )


def _matches_query(source: dict[str, object], query: str) -> bool:
    """Check if a source matches a free-text query (case-insensitive)."""
    query_lower = query.lower()
    searchable_fields = (
        str(source.get("authors", "")),
        str(source.get("title", "")),
        str(source.get("journal", "")),
        str(source.get("doi", "")),
    )
    return any(query_lower in field.lower() for field in searchable_fields)


def register_source_tools(mcp: FastMCP) -> None:
    """Register literature source MCP tools."""

    @mcp.tool(
        name="search_sources",
        annotations={
            "title": "Search Literature Sources",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def search_sources(
        *,
        query: str,
        source_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Search the NFM literature source database.

        Find journal articles, technical reports, handbooks, and other
        references that are cited as data sources in the database.

        Returns:
            JSON array of source records with id, authors, title, year,
            and citation count.
        """
        results = list(SOURCES)

        if source_type is not None:
            type_lower = source_type.lower()
            results = [
                s for s in results
                if str(s.get("source_type", "")).lower() == type_lower
            ]

        if query:
            results = [s for s in results if _matches_query(s, query)]

        paginated = results[offset : offset + limit]
        return json.dumps(paginated, default=str)
