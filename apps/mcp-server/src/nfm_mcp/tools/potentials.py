"""Thermodynamic potential query tools."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.tools.mock_data import POTENTIALS


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


def _parse_temperature_range(temp_range_str: str) -> tuple[float, float] | None:
    """Parse a temperature range string like '300-3000 K' into (low, high)."""
    cleaned = temp_range_str.replace("K", "").replace("k", "").strip()
    parts = cleaned.split("-")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None


def _ranges_overlap(
    valid_range: list[object],
    filter_range: tuple[float, float],
) -> bool:
    """Check if two temperature ranges overlap."""
    if len(valid_range) < 2:
        return False
    valid_low = float(valid_range[0])
    valid_high = float(valid_range[1])
    filter_low, filter_high = filter_range
    return valid_low <= filter_high and valid_high >= filter_low


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
    async def query_potentials(
        *,
        material_id: str,
        potential_type: str | None = None,
        model_name: str | None = None,
        temperature_range: str | None = None,
    ) -> str:
        """Query thermodynamic potential models for a nuclear material.

        Retrieves Gibbs energy, enthalpy, entropy, heat capacity, and
        other thermodynamic property models with their parametric
        coefficients and valid temperature ranges.

        Returns:
            JSON array of potential model records with model name,
            expression type, coefficients, and valid range.
        """
        results = [
            p for p in POTENTIALS
            if p.get("material_id") == material_id
        ]

        if potential_type is not None:
            type_lower = potential_type.lower()
            results = [
                p for p in results
                if type_lower in str(p.get("potential_type", "")).lower()
            ]

        if model_name is not None:
            name_lower = model_name.lower()
            results = [
                p for p in results
                if name_lower in str(p.get("model_name", "")).lower()
            ]

        if temperature_range is not None:
            parsed = _parse_temperature_range(temperature_range)
            if parsed is not None:
                results = [
                    p for p in results
                    if isinstance(p.get("valid_range_k"), list)
                    and _ranges_overlap(list(p["valid_range_k"]), parsed)
                ]

        return json.dumps(results, default=str)
