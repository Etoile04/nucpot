"""Material search and retrieval tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


class SearchMaterialsInput(BaseModel):
    """Input for searching nuclear fuel materials."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Free-text search query (e.g., 'UO2 thermal conductivity')",
        min_length=1,
        max_length=500,
    )
    material_type: Optional[str] = Field(
        default=None,
        description="Filter by material type (e.g., 'fuel', 'cladding', 'coolant')",
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


class GetMaterialInput(BaseModel):
    """Input for retrieving a single material by ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    material_id: str = Field(
        ...,
        description="Unique material identifier (e.g., 'UO2', 'Zircaloy-4')",
        min_length=1,
        max_length=200,
    )


def register_material_tools(mcp: FastMCP) -> None:
    """Register material-related MCP tools."""

    @mcp.tool(
        name="search_materials",
        annotations={
            "title": "Search Nuclear Materials",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def search_materials(params: SearchMaterialsInput) -> str:
        """Search the NFM database for nuclear fuel and materials.

        Performs full-text search across material names, compositions, and
        descriptions. Results are ranked by relevance.

        Returns:
            JSON array of matching materials with id, name, type, and
            description fields.
        """
        return "[]"

    @mcp.tool(
        name="get_material",
        annotations={
            "title": "Get Material Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_material(params: GetMaterialInput) -> str:
        """Retrieve detailed information about a specific nuclear material.

        Returns the full material record including composition, crystal
        structure, and available property data categories.

        Returns:
            JSON object with material details or an error string if not found.
        """
        return '{"error": "not implemented"}'
