"""Material property query tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


class QueryPropertiesInput(BaseModel):
    """Input for querying material properties."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    material_id: str = Field(
        ...,
        description="Material UUID identifier to query properties for",
        min_length=1,
        max_length=200,
    )
    property_name: str | None = Field(
        default=None,
        description="Specific property name filter (e.g., 'thermal_conductivity')",
    )
    temperature_range: str | None = Field(
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
        annotations=ToolAnnotations(
            title="Query Material Properties",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def query_properties(
        *,
        material_id: str,
        property_name: str | None = None,
        temperature_range: str | None = None,
        limit: int = 50,
    ) -> str:
        """Query property data for a specific nuclear material.

        Retrieves measured and calculated property values including
        thermal conductivity, density, specific heat, Young's modulus,
        thermal expansion, and more.

        Returns:
            JSON array of property data points with temperature,
            value, unit, and source reference.
        """
        try:
            from nfm_db.services.property_service import list_measurements

            # Validate material_id as UUID
            try:
                material_uuid = uuid.UUID(material_id)
            except ValueError:
                return json.dumps({
                    "error": (
                        f"Invalid material_id '{material_id}'. "
                        "Must be a valid UUID."
                    ),
                })

            page = 1
            per_page = limit

            async for db in get_db_session():
                result = await list_measurements(
                    db,
                    page=page,
                    per_page=per_page,
                    material_id=material_uuid,
                )
                return str(result.model_dump_json(indent=2))

            # Fallback: get_db_session always yields once, but mypy needs
            # an explicit return on all paths.
            return json.dumps({"error": "No database session available"})

        except Exception as exc:
            logger.exception("query_properties failed")
            return json.dumps({"error": f"Property query failed: {exc}"})
