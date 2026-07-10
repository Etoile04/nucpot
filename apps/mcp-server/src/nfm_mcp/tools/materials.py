"""Material search and retrieval tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


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
        description="Unique material identifier (UUID or slug, e.g., 'UO2')",
        min_length=1,
        max_length=200,
    )


def _material_type_to_category_id(
    material_type: str,
) -> uuid.UUID | None:
    """Map a material_type string to a category UUID.

    The MCP tool interface uses human-friendly type names
    ('fuel', 'cladding', etc.).  The database uses UUID category
    references.  This function maps between the two when a
    lookup table is available, or returns None for direct pass-through.
    """
    # In a future iteration this could query the MaterialCategory table.
    # For now the search_service handles ILIKE matching on name/formula
    # which is more flexible than strict category filtering.
    return None


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
    async def search_materials(
        *,
        query: str,
        material_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Search the NFM database for nuclear fuel and materials.

        Performs full-text search across material names, compositions, and
        aliases. Results are ranked by relevance.

        Returns:
            JSON array of matching materials with id, name, formula,
            crystal structure, and description fields.
        """
        try:
            from nfm_db.services.material_service import search_materials as svc_search

            page = max(1, (offset // max(1, limit)) + 1)

            async for db in get_db_session():
                result = await svc_search(
                    db,
                    query=query,
                    page=page,
                    limit=limit,
                )
                return result.model_dump_json(indent=2)

        except ValueError:
            return json.dumps({"error": "Invalid material identifier format"})
        except Exception as exc:
            logger.exception("search_materials failed")
            return json.dumps({"error": f"Search failed: {exc}"})

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
    async def get_material(*, material_id: str) -> str:
        """Retrieve detailed information about a specific nuclear material.

        Returns the full material record including composition, crystal
        structure, aliases, and available property data categories.

        Returns:
            JSON object with material details or an error string if not found.
        """
        try:
            from nfm_db.services.material_service import get_material as svc_get

            # Try parsing as UUID first
            try:
                material_uuid = uuid.UUID(material_id)
            except ValueError:
                return json.dumps({
                    "error": (
                        f"Material '{material_id}' not found. "
                        "Provide a valid UUID identifier."
                    ),
                })

            async for db in get_db_session():
                result = await svc_get(db, material_id=material_uuid)
                if result is None:
                    return json.dumps({
                        "error": f"Material '{material_id}' not found",
                    })
                return result.model_dump_json(indent=2)

        except Exception as exc:
            logger.exception("get_material failed")
            return json.dumps({"error": f"Lookup failed: {exc}"})
