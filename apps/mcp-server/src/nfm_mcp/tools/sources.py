"""Literature source search tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


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
        description="Filter by source type (e.g., 'journal_article', 'report', 'book')",
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


class GetSourceInput(BaseModel):
    """Input for retrieving a single source by ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source_id: str = Field(
        ...,
        description="Unique source identifier (UUID)",
        min_length=1,
        max_length=200,
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
            JSON object with paginated source records including id, title,
            authors, year, and source type.
        """
        try:
            from nfm_db.services.source_service import list_sources

            page = max(1, (offset // max(1, limit)) + 1)

            async for db in get_db_session():
                result = await list_sources(
                    db,
                    source_type=source_type,
                    page=page,
                    per_page=limit,
                )
                return result.model_dump_json(indent=2)

        except Exception as exc:
            logger.exception("search_sources failed")
            return json.dumps({"error": f"Search failed: {exc}"})

    @mcp.tool(
        name="get_source",
        annotations={
            "title": "Get Source Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_source(*, source_id: str) -> str:
        """Retrieve detailed information about a specific literature source.

        Returns the full source record including authors, DOI, abstract,
        and journal details.

        Returns:
            JSON object with source details or an error string if not found.
        """
        try:
            from nfm_db.services.source_service import get_source

            try:
                source_uuid = uuid.UUID(source_id)
            except ValueError:
                return json.dumps({
                    "error": (
                        f"Source '{source_id}' not found. "
                        "Provide a valid UUID identifier."
                    ),
                })

            async for db in get_db_session():
                result = await get_source(db, source_id=source_uuid)
                if result is None:
                    return json.dumps({
                        "error": f"Source '{source_id}' not found",
                    })
                return result.model_dump_json(indent=2)

        except Exception as exc:
            logger.exception("get_source failed")
            return json.dumps({"error": f"Lookup failed: {exc}"})
