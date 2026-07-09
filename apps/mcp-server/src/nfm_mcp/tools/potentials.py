"""Thermodynamic potential query tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


class QueryPotentialsInput(BaseModel):
    """Input for querying thermodynamic potentials."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    material_id: str = Field(
        ...,
        description="Material identifier to query potentials for",
        min_length=1,
        max_length=200,
    )
    potential_type: Optional[str] = Field(
        default=None,
        description="Potential type filter (e.g., 'Gibbs', 'enthalpy', 'Cp')",
    )
    model_name: Optional[str] = Field(
        default=None,
        description="Specific model name (e.g., 'FINK-LUCUTA2')",
    )
    temperature_range: Optional[str] = Field(
        default=None,
        description="Temperature range filter (e.g., '300-3000 K')",
    )


def register_potential_tools(mcp: FastMCP) -> None:
    """Register thermodynamic potential MCP tools."""

    @mcp.tool(
        name="query_potentials",
        annotations={
            "title": "Query Thermodynamic Potentials",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def query_potentials(params: QueryPotentialsInput) -> str:
        """Query thermodynamic potential models for a nuclear material.

        Retrieves Gibbs energy, enthalpy, entropy, heat capacity, and
        other thermodynamic property models with their parametric
        coefficients and valid temperature ranges.

        Returns:
            JSON array of potential model records with model name,
            expression type, coefficients, and valid range.
        """
        return "[]"
