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
        description="Start browsing from this ontology node ID (maps to corpus_id)",
    )
    limit: int = Field(
        default=50,
        description="Maximum nodes to return (1-200)",
        ge=1,
        le=200,
    )


def _filter_nodes_by_query(
    nodes: list[dict[str, object]],
    query: str,
) -> list[dict[str, object]]:
    """Post-hoc filter ontology nodes by search query."""
    query_lower = query.lower()
    return [
        n for n in nodes
        if query_lower in str(n.get("label", "")).lower()
        or query_lower in str(n.get("name", "")).lower()
        or query_lower in str(n.get("id", "")).lower()
    ]


def _filter_nodes_by_type(
    nodes: list[dict[str, object]],
    entity_type: str,
) -> list[dict[str, object]]:
    """Post-hoc filter ontology nodes by entity type."""
    type_lower = entity_type.lower()
    return [
        n for n in nodes
        if str(n.get("type", "")).lower() == type_lower
    ]


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

        When parent_id is provided, derives the ontology graph from
        staging data for that corpus. Otherwise returns an error prompting
        the caller to specify a corpus.

        Returns:
            JSON object with ontology nodes, relationships, stats, and
            optional pagination cursor.
        """
        if parent_id is None:
            return json.dumps({
                "error": "parent_id (corpus_id) is required to browse the ontology",
            })

        try:
            from nfm_db.services.ontology_service import (
                CorpusNotFoundError,
                derive_ontology_graph,
            )

            async for db in get_db_session():
                graph = await derive_ontology_graph(
                    db,
                    corpus_id=parent_id,
                    max_nodes=limit,
                )

                result = graph.model_dump()
                nodes = result.get("nodes", [])

                # Post-hoc filtering on the derived graph
                if entity_type is not None:
                    nodes = _filter_nodes_by_type(nodes, entity_type)
                if query is not None:
                    nodes = _filter_nodes_by_query(nodes, query)

                result["nodes"] = nodes
                return json.dumps(result, default=str)

        except CorpusNotFoundError:
            return json.dumps({
                "error": f"Corpus '{parent_id}' not found",
            })
        except Exception as exc:
            logger.exception("browse_ontology failed")
            return json.dumps({"error": f"Ontology lookup failed: {exc}"})
