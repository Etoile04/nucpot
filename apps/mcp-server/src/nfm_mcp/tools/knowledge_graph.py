"""Knowledge graph query tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


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
            from nfm_db.models.kg import KGEdge, KGNode

            pattern = f"%{query}%"

            async for db in get_db_session():
                # Build base query for KGNode with ILIKE on label
                stmt = select(KGNode).where(
                    or_(
                        KGNode.label.ilike(pattern),
                        KGNode.node_type.ilike(pattern),
                    )
                )

                # Filter by entity types if provided
                if entity_types is not None:
                    allowed = {t.title() for t in entity_types}
                    stmt = stmt.where(KGNode.node_type.in_(allowed))

                # Filter to active nodes only
                stmt = stmt.where(KGNode.status == "active")

                # Order by confidence descending
                stmt = stmt.order_by(KGNode.confidence.desc())
                stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                nodes = result.scalars().all()

                # Collect matching node IDs for edge retrieval
                node_ids = {n.id for n in nodes}

                # Fetch edges connected to matching nodes
                edge_stmt = select(KGEdge).where(
                    or_(
                        KGEdge.source_node_id.in_(node_ids),
                        KGEdge.target_node_id.in_(node_ids),
                    )
                ).limit(limit * 3)

                edge_result = await db.execute(edge_stmt)
                edges = edge_result.scalars().all()

                response = {
                    "nodes": [
                        {
                            "id": str(n.id),
                            "label": n.label,
                            "entity_type": n.node_type,
                            "confidence": n.confidence,
                            "properties": n.properties,
                            "source_id": str(n.source_id) if n.source_id else None,
                        }
                        for n in nodes
                    ],
                    "edges": [
                        {
                            "id": str(e.id),
                            "source": str(e.source_node_id),
                            "target": str(e.target_node_id),
                            "relation_type": e.relation_type,
                            "confidence": e.confidence,
                            "properties": e.properties,
                        }
                        for e in edges
                    ],
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                }
                return json.dumps(response, default=str)

        except Exception as exc:
            logger.exception("query_knowledge_graph failed")
            return json.dumps({"error": f"Knowledge graph query failed: {exc}"})
