"""Thermodynamic potential query tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
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
        try:
            from nfm_db.services.potential_service import list_potentials

            # Use material_id as text query; model_name refines if both given
            search_query = model_name if model_name else material_id

            async for db in get_db_session():
                result = await list_potentials(
                    db,
                    page=1,
                    limit=100,
                    type_filter=potential_type,
                    query=search_query,
                )

                # Post-filter by model_name if both material_id and model_name provided
                items = result.potentials
                if model_name and material_id:
                    name_lower = model_name.lower()
                    items = [
                        p for p in items
                        if name_lower in (p.name or "").lower()
                        or name_lower in (p.display_name or "").lower()
                    ]

                return result.model_dump_json(indent=2)

        except Exception as exc:
            logger.exception("query_potentials failed")
            return json.dumps({"error": f"Potential query failed: {exc}"})
