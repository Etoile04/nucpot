"""Knowledge graph query tools."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.tools.mock_data import KG_EDGES, KG_NODES


class QueryKnowledgeGraphInput(BaseModel):
    """Input for querying the NFM knowledge graph."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural-language or Cypher-like query (e.g., 'materials with thermal conductivity > 10 W/mK')",
        min_length=1,
        max_length=1000,
    )
    entity_types: Optional[list[str]] = Field(
        default=None,
        description="Filter by entity types (e.g., ['material', 'property', 'source'])",
    )
    limit: int = Field(
        default=20,
        description="Maximum results to return (1-100)",
        ge=1,
        le=100,
    )


def _query_matches(node: dict[str, object], query: str) -> bool:
    """Check if a KG node label matches a query term."""
    query_lower = query.lower()
    label = str(node.get("label", "")).lower()
    entity_type = str(node.get("entity_type", "")).lower()
    return query_lower in label or query_lower in entity_type


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
        matching_node_ids: set[str] = set()

        filtered_nodes = list(KG_NODES)

        if entity_types is not None:
            allowed = {t.lower() for t in entity_types}
            filtered_nodes = [
                n for n in filtered_nodes
                if str(n.get("entity_type", "")).lower() in allowed
            ]

        for node in filtered_nodes:
            if _query_matches(node, query):
                matching_node_ids.add(str(node.get("id", "")))

        edges = [
            e for e in KG_EDGES
            if str(e.get("source", "")) in matching_node_ids
            or str(e.get("target", "")) in matching_node_ids
        ]

        result_nodes = filtered_nodes[: limit]
        return json.dumps(
            {"nodes": result_nodes, "edges": edges},
            default=str,
        )
