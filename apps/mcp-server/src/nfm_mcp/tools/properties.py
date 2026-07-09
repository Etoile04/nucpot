"""Material property query tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


class QueryPropertiesInput(BaseModel):
    """Input for querying material properties."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    material_id: str = Field(
        ...,
        description="Material identifier to query properties for",
        min_length=1,
        max_length=200,
    )
    property_name: Optional[str] = Field(
        default=None,
        description="Specific property name (e.g., 'thermal_conductivity', 'density')",
    )
    temperature_range: Optional[str] = Field(
        default=None,
        description="Temperature range filter (e.g., '300-1500 K')",
    )
    limit: int = Field(
        default=50,
        description="Maximum data points to return (1-500)",
        ge=1,
        le=500,
    )


def register_property_tools(mcp: FastMCP) -> None:
    """Register property-query MCP tools."""

    @mcp.tool(
        name="query_properties",
        annotations={
            "title": "Query Material Properties",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def query_properties(params: QueryPropertiesInput) -> str:
        """Query property data for a specific nuclear material.

        Retrieves measured and calculated property values including
        thermal conductivity, density, specific heat, Young's modulus,
        thermal expansion, and more.

        Returns:
            JSON array of property data points with temperature,
            value, unit, and source reference.
        """
        return "[]"
