"""Thermodynamic potential query tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


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


class GetPotentialInput(BaseModel):
    """Input for retrieving a single potential by ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    potential_id: str = Field(
        ...,
        description="Unique potential identifier (UUID)",
        min_length=1,
        max_length=200,
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
            JSON object with paginated potential records including name,
            type, elements, and description.
        """
        try:
            from nfm_db.services.potential_service import list_potentials

            async for db in get_db_session():
                result = await list_potentials(
                    db,
                    type_filter=potential_type,
                    query=model_name,
                    page=1,
                    limit=100,
                )

                response_data = result.model_dump()

                # Client-side post-filter: temperature range overlap
                if temperature_range is not None:
                    parsed = _parse_temperature_range(temperature_range)
                    if parsed is not None:
                        response_data["potentials"] = [
                            p for p in response_data["potentials"]
                            if isinstance(p.get("applicability", {}).get("temperature_range"), list)
                            and _ranges_overlap(
                                list(p["applicability"]["temperature_range"]),
                                parsed,
                            )
                        ]

                return json.dumps(response_data, default=str, indent=2)

        except Exception as exc:
            logger.exception("query_potentials failed")
            return json.dumps({"error": f"Query failed: {exc}"})

    @mcp.tool(
        name="get_potential",
        annotations={
            "title": "Get Potential Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_potential(*, potential_id: str) -> str:
        """Retrieve detailed information about a specific thermodynamic potential.

        Returns the full potential record including coefficients,
        applicability ranges, references, and verification data.

        Returns:
            JSON object with potential details or an error string if not found.
        """
        try:
            from nfm_db.services.potential_service import get_potential_by_id

            try:
                potential_uuid = uuid.UUID(potential_id)
            except ValueError:
                return json.dumps({
                    "error": (
                        f"Potential '{potential_id}' not found. "
                        "Provide a valid UUID identifier."
                    ),
                })

            async for db in get_db_session():
                result = await get_potential_by_id(db, potential_id=potential_uuid)
                if result is None:
                    return json.dumps({
                        "error": f"Potential '{potential_id}' not found",
                    })
                return result.model_dump_json(indent=2)

        except Exception as exc:
            logger.exception("get_potential failed")
            return json.dumps({"error": f"Lookup failed: {exc}"})
