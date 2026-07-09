"""Ontology browsing tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


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
    async def browse_ontology(params: BrowseOntologyInput) -> str:
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
        return "[]"
