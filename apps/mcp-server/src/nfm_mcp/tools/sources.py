"""Literature source search tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


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
    async def search_sources(params: SearchSourcesInput) -> str:
        """Search the NFM literature source database.

        Find journal articles, technical reports, handbooks, and other
        references that are cited as data sources in the database.

        Returns:
            JSON array of source records with id, authors, title, year,
            and citation count.
        """
        return "[]"
