"""Ontology browsing tools."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.tools.mock_data import ONTOLOGY


class BrowseOntologyInput(BaseModel):
    """Input for browsing the NFM ontology."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Search term within the ontology (e.g., 'fuel', 'thermal')",
    )
    entity_type: Optional[str] = Field(
        default=None,
        description="Filter by entity type (e.g., 'material', 'property', 'source')",
    )
    parent_id: Optional[str] = Field(
        default=None,
        description="Start browsing from this ontology node ID",
    )
    limit: int = Field(
        default=50,
        description="Maximum nodes to return (1-200)",
        ge=1,
        le=200,
    )


def _matches_query(node: dict[str, object], query: str) -> bool:
    """Check if an ontology node matches a search query."""
    query_lower = query.lower()
    searchable_fields = (
        str(node.get("label", "")),
        str(node.get("description", "")),
        node.get("id", ""),
    )
    return any(query_lower in field.lower() for field in searchable_fields)


def register_ontology_tools(mcp: FastMCP) -> None:
    """Register ontology browsing MCP tools."""

    @mcp.tool(
        name="browse_ontology",
        annotations={
            "title": "Browse NFM Ontology",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def browse_ontology(
        *,
        query: str | None = None,
        entity_type: str | None = None,
        parent_id: str | None = None,
        limit: int = 50,
    ) -> str:
        """Browse the NFM domain ontology tree.

        The ontology defines the hierarchical classification of nuclear
        fuel materials, their properties, measurement types, and
        relationships. Useful for discovering valid search terms and
        understanding the data model.

        Returns:
            JSON array of ontology nodes with id, label, type, and
            children count. If query is provided, returns matching
            nodes; otherwise returns top-level nodes.
        """
        results = list(ONTOLOGY)

        if parent_id is not None:
            results = [
                n for n in results
                if n.get("parent_id") == parent_id
            ]

        if entity_type is not None:
            type_lower = entity_type.lower()
            results = [
                n for n in results
                if str(n.get("entity_type", "")).lower() == type_lower
            ]

        if query is not None:
            results = [n for n in results if _matches_query(n, query)]

        paginated = results[: limit]
        return json.dumps(paginated, default=str)
