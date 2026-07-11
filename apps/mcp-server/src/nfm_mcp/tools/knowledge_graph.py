"""Knowledge graph query tools (wired to real service layer)."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alias normalization: lowercase MCP input → canonical DB node_type
# ---------------------------------------------------------------------------

_ENTITY_TYPE_ALIASES: dict[str, str] = {
    "material": "Material",
    "property": "Property",
    "experiment": "Experiment",
    "condition": "Condition",
    "publication": "Publication",
}


# ---------------------------------------------------------------------------
# Input schema (for OpenAPI docs / validation reference)
# ---------------------------------------------------------------------------


class QueryKnowledgeGraphInput(BaseModel):
    """Input for querying the NFM knowledge graph."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural-language or Cypher-like query (e.g., 'materials with thermal conductivity > 10 W/mK')",
        min_length=1,
        max_length=1000,
    )
    entity_types: list[str] | None = Field(
        default=None,
        description="Filter by entity types (e.g., ['material', 'property', 'source'])",
    )
    limit: int = Field(
        default=20,
        description="Maximum results to return (1-100)",
        ge=1,
        le=100,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_entity_types(
    raw: list[str] | None,
) -> list[str] | None:
    """Normalize entity type aliases to canonical DB node_type values.

    Examples:
        ['material'] → ['Material']
        ['Material'] → ['Material']
        ['fuel'] → ['Fuel']  (unknown types are capitalized as-is)
    """
    if raw is None:
        return None

    normalized: set[str] = set()
    for t in raw:
        stripped = t.strip()
        if not stripped:
            continue
        canonical = _ENTITY_TYPE_ALIASES.get(stripped.lower())
        if canonical:
            normalized.add(canonical)
        else:
            normalized.add(stripped[0].upper() + stripped[1:])

    return sorted(normalized) if normalized else None


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a KGNode ORM object to a JSON-serializable dict."""
    return {
        "id": str(node.id),
        "node_type": node.node_type,
        "label": node.label,
        "aliases": node.aliases,
        "properties": node.properties,
        "confidence": node.confidence,
        "status": node.status,
        "source_id": str(node.source_id) if node.source_id else None,
        "corpus_id": node.corpus_id,
    }


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    """Convert a KGEdge ORM object to a JSON-serializable dict."""
    return {
        "id": str(edge.id),
        "source_node_id": str(edge.source_node_id),
        "target_node_id": str(edge.target_node_id),
        "relation_type": edge.relation_type,
        "properties": edge.properties,
        "confidence": edge.confidence,
        "source_id": str(edge.source_id) if edge.source_id else None,
        "corpus_id": edge.corpus_id,
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


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
            JSON object with nodes and edges representing the
            matching subgraph.
        """
        try:
            from nfm_db.services.kg_re import (
                query_graph_edges,
                query_graph_nodes,
            )

            normalized_types = _normalize_entity_types(entity_types)

            async for db in get_db_session():
                nodes = await query_graph_nodes(
                    db,
                    entity_types=normalized_types,
                    query=query,
                    limit=limit,
                )

                node_ids = {n.id for n in nodes}
                edges = await query_graph_edges(
                    db,
                    node_ids=node_ids,
                    limit=limit * 3,
                )

                return json.dumps(
                    {
                        "nodes": [_node_to_dict(n) for n in nodes],
                        "edges": [_edge_to_dict(e) for e in edges],
                    },
                    default=str,
                )

        except Exception as exc:
            logger.exception("query_knowledge_graph failed")
            return json.dumps({"error": f"Knowledge graph query failed: {exc}"})
