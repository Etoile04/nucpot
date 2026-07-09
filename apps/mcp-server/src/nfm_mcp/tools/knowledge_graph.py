"""Knowledge graph query tools."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


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
    async def query_knowledge_graph(params: QueryKnowledgeGraphInput) -> str:
        """Query the NFM knowledge graph for material relationships.

        The knowledge graph connects materials, properties, sources,
        and measurement conditions into a semantic network. Use this
        tool for complex cross-referencing queries.

        Returns:
            JSON object with nodes and edges representing the
            matching subgraph.
        """
        return '{"nodes": [], "edges": []}'
