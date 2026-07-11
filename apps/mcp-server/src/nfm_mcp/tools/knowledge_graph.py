"""Knowledge graph query tools (NFM-1135 — Phase B: real service layer).

Wraps :mod:`nfm_db.services.kg_re` for read-only KG queries.
The tool receives a free-text ``query`` and optional ``entity_types``
filter, normalizes entity types to the PascalCase values used by
the ORM (``Material``, ``Property``, ``Experiment``, ``Condition``,
``Publication``), and returns the matching subgraph as ``nodes`` and
``edges`` arrays.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


# Mapping from friendly (lowercase, mock-data-style) entity_type strings
# to the PascalCase node_type values enforced by the KG ORM.  Unknown
# values are passed through unchanged so the service layer can decide.
_ENTITY_TYPE_ALIASES: dict[str, str] = {
    "material": "Material",
    "property": "Property",
    "properties": "Property",
    "experiment": "Experiment",
    "condition": "Condition",
    "publication": "Publication",
}


class QueryKnowledgeGraphInput(BaseModel):
    """Input for querying the NFM knowledge graph."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description=(
            "Free-text search term matched against node labels "
            "(e.g., 'UO2', 'thermal conductivity')"
        ),
        min_length=1,
        max_length=1000,
    )
    entity_types: Optional[list[str]] = Field(
        default=None,
        description=(
            "Filter by entity types "
            "(e.g., ['material', 'property', 'source'])"
        ),
    )
    limit: int = Field(
        default=20,
        description="Maximum results to return (1-100)",
        ge=1,
        le=100,
    )


def _normalize_entity_types(
    entity_types: list[str] | None,
) -> list[str] | None:
    """Map friendly entity_type names to ORM PascalCase node_types.

    Unknown values are passed through unchanged so they can be filtered
    out by the service layer (which validates against VALID_NODE_TYPES).
    """
    if entity_types is None:
        return None
    return [
        _ENTITY_TYPE_ALIASES.get(t.lower(), t) for t in entity_types
    ]


def register_kg_tools(mcp: FastMCP) -> None:
    """Register knowledge graph MCP tools."""

    @mcp.tool(
        name="query_knowledge_graph",
        annotations={
            "title": "Query Knowledge Graph",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def query_knowledge_graph(
        *,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """Query the NFM knowledge graph for material relationships.

        The knowledge graph connects materials, properties, sources,
        and measurement conditions into a semantic network. Use this
        tool for complex cross-referencing queries.

        Returns:
            JSON object with ``nodes`` and ``edges`` arrays representing
            the matching subgraph.  On failure returns
            ``{"error": "..."}``.
        """
        normalized_types = _normalize_entity_types(entity_types)
        try:
            from nfm_db.services.kg_re import (
                query_graph_edges,
                query_graph_nodes,
            )

            async for db in get_db_session():
                nodes = await query_graph_nodes(
                    db,
                    entity_types=normalized_types,
                    query=query,
                    limit=limit,
                )
                edges = await query_graph_edges(db, limit=limit)
                return json.dumps(
                    {"nodes": nodes, "edges": edges},
                    default=str,
                )
        except Exception as exc:
            logger.exception("query_knowledge_graph failed")
            return json.dumps({"error": f"Query failed: {exc}"})