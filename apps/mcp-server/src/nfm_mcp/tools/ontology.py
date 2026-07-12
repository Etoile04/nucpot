"""Ontology browsing tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


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
        try:
            from nfm_db.services.ontology_service import (
                derive_ontology_graph,
            )

            # Map the MCP 'query' param to the service's corpus_id/source param
            corpus_id = query if query else "nfmd/ref-gap-fill"

            async for db in get_db_session():
                result = await derive_ontology_graph(
                    db,
                    corpus_id=corpus_id,
                    limit=limit,
                )

                # Post-filter by entity_type if requested
                if entity_type:
                    type_lower = entity_type.lower()
                    nodes = [
                        n for n in result.nodes
                        if type_lower in (n.type or "").lower()
                    ]
                    node_ids = {n.id for n in nodes}
                    relationships = [
                        r for r in result.relationships
                        if r.from_ in node_ids or r.to in node_ids
                    ]
                else:
                    nodes = result.nodes
                    relationships = result.relationships

                # Post-filter by parent_id if requested
                if parent_id:
                    nodes = [
                        n for n in nodes
                        if n.id == parent_id
                    ]

                response = {
                    "nodes": [n.model_dump(mode="json") for n in nodes[:limit]],
                    "relationships": [
                        r.model_dump(mode="json") for r in relationships
                    ],
                    "stats": result.stats.model_dump(mode="json"),
                }
                return json.dumps(response, default=str)

        except Exception as exc:
            logger.exception("browse_ontology failed")
            return json.dumps({"error": f"Ontology browse failed: {exc}"})
