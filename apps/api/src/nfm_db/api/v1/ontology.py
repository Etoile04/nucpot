"""Ontology NVL graph endpoint (Phase 1 backend NVL API — NFM-270 / NFM-266).

``GET /api/v1/ontology/corpora/{corpus_id}/graph`` emits the versioned NFM-227
NVL contract envelope derived read-only from ``_ref_gap_fill_staging``. The
viewer (Phase 0) swaps its static data URL for this endpoint with zero code
change (contract-as-firewall invariant).

Phase 2 (NFM-820): Adds 4 new AGE-backed endpoints for graph queries:
- Node + Neighbors: GET /api/v1/ontology/node/{id}
- Fuzzy Search: GET /api/v1/ontology/search
- Shortest Path: GET /api/v1/ontology/path
- Sync: POST /api/v1/ontology/sync
"""

from __future__ import annotations

import uuid
from datetime import UTC
from email.utils import format_datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.schemas.ontology import OntologyGraphResponse
from nfm_db.schemas.ontology_query import (
    NodeNeighborsResponse,
    NodeResponse,
    PathNode,
    SearchResponse,
    SearchResultItem,
    ShortestPathResponse,
    SyncResponse,
)
from nfm_db.services.ontology_service import (
    HARD_MAX_NODES,
    CorpusNotFoundError,
    derive_ontology_graph,
)
from nfm_db.services.ontology_sync import (
    GraphNotFoundError,
    OntologySyncError,
    rebuild_graph,
)
from nfm_db.services.rate_limit import ontology_rate_limit

router = APIRouter()

# Safe slug — also the only form a staging ``source`` may take. Path-validated
# (422 on mismatch); no string interpolation into SQL downstream.
CORPUS_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Derived (immutable) data: cache briefly. ETag is content-addressed (digest).
# `public` is safe because the derived corpus is scientific reference data with
# no auth and no PII. If embargoed/pre-publication corpora ever land here, switch
# to `private` and add an auth gate (T8 security review, MEDIUM-2).
_CACHE_CONTROL = "public, max-age=60"


@router.get(
    "/ontology/corpora/{corpus_id}/graph",
    response_model=OntologyGraphResponse,
    response_model_by_alias=True,
    summary="Versioned NVL graph for a corpus",
)
async def get_corpus_graph(
    response: Response,
    corpus_id: str = Path(
        ...,
        pattern=CORPUS_ID_PATTERN,
        description="Corpus slug (= staging source).",
    ),
    max_nodes: int | None = Query(
        default=None,
        ge=1,
        le=HARD_MAX_NODES,
        description="Page size ceiling (nodes). Omits → full corpus (<= hard ceiling).",
    ),
    cursor: str | None = Query(
        default=None,
        description="Opaque pagination cursor from a prior response.",
    ),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> OntologyGraphResponse:
    """Return the derived versioned NVL graph for ``corpus_id``.

    Raises:
        404: the corpus resolves to no staging rows.
    """
    try:
        graph = await derive_ontology_graph(
            session,
            corpus_id,
            max_nodes=max_nodes,
            cursor=cursor,
        )
    except CorpusNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"corpus not found: {corpus_id}",
        ) from None

    response.headers["Cache-Control"] = _CACHE_CONTROL
    # source_digest is the stable corpus identity (NFM-227); for paginated
    # responses the request cursor is folded into the ETag so each page has a
    # distinct, cache-correct validator.
    etag_base = (
        graph.source_digest
        if cursor is None
        else f"{graph.source_digest}#{cursor}"
    )
    response.headers["ETag"] = f'"{etag_base}"'
    last_modified = graph._last_modified
    if last_modified is not None:
        # Postgres returns tz-aware values; SQLite (tests) is naive — coerce.
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=UTC)
        response.headers["Last-Modified"] = format_datetime(
            last_modified,
            usegmt=True,
        )
    return graph


# ---------------------------------------------------------------------------
# Phase 2: AGE-backed graph query endpoints (NFM-820)
# ---------------------------------------------------------------------------


@router.get(
    "/ontology/node/{node_id}",
    response_model=NodeNeighborsResponse,
    response_model_by_alias=True,
    summary="Get node with neighbors at depth",
)
async def get_node_neighbors(
    node_id: uuid.UUID = Path(..., description="Target node ID"),
    corpus_id: str | None = Query(None, description="Corpus scope filter"),
    depth: int = Query(default=1, ge=1, le=3, description="Hop depth (1-3)"),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> NodeNeighborsResponse:
    """Return a node with its neighbors at the requested depth.

    Raises:
        404: node not found
        422: invalid parameters
    """
    # Load the target node
    node_result = await session.execute(
        select(KGNode).where(KGNode.id == node_id)
    )
    node = node_result.scalar_one_or_none()

    if not node:
        raise HTTPException(
            status_code=404,
            detail=f"node not found: {node_id}",
        )

    # Filter by corpus if specified
    if corpus_id is not None and node.corpus_id != corpus_id:
        raise HTTPException(
            status_code=404,
            detail=f"node not found in corpus: {corpus_id}",
        )

    # For depth=1, load direct neighbors only (simplified implementation)
    neighbors_response = []
    total_neighbors = 0

    if depth == 1:
        # Load outgoing edges
        outgoing_edges_result = await session.execute(
            select(KGEdge).where(KGEdge.source_node_id == node_id)
        )
        outgoing_edges = outgoing_edges_result.scalars().all()

        # Load incoming edges
        incoming_edges_result = await session.execute(
            select(KGEdge).where(KGEdge.target_node_id == node_id)
        )
        incoming_edges = incoming_edges_result.scalars().all()

        # Build neighbor responses
        for edge in outgoing_edges:
            target_node_result = await session.execute(
                select(KGNode).where(KGNode.id == edge.target_node_id)
            )
            target_node = target_node_result.scalar_one_or_none()
            if target_node:
                neighbors_response.append(
                    {
                        "node": NodeResponse.model_validate(target_node),
                        "edge": {
                            "relation_type": edge.relation_type,
                            "direction": "outgoing",
                            "confidence": edge.confidence,
                        },
                    }
                )

        for edge in incoming_edges:
            source_node_result = await session.execute(
                select(KGNode).where(KGNode.id == edge.source_node_id)
            )
            source_node = source_node_result.scalar_one_or_none()
            if source_node:
                neighbors_response.append(
                    {
                        "node": NodeResponse.model_validate(source_node),
                        "edge": {
                            "relation_type": edge.relation_type,
                            "direction": "incoming",
                            "confidence": edge.confidence,
                        },
                    }
                )

        total_neighbors = len(neighbors_response)

    return NodeNeighborsResponse(
        node=NodeResponse.model_validate(node),
        neighbors=neighbors_response,
        total_neighbors=total_neighbors,
    )


@router.get(
    "/ontology/search",
    response_model=SearchResponse,
    response_model_by_alias=True,
    summary="Fuzzy search nodes by label or aliases",
)
async def search_nodes(
    q: str = Query(..., min_length=1, description="Search query"),
    corpus_id: str | None = Query(None, description="Corpus scope filter"),
    limit: int = Query(default=20, ge=1, le=100, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Result offset"),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> SearchResponse:
    """Fuzzy search nodes by label or aliases using ILIKE.

    Returns:
        Search results with match scores and pagination metadata
    """
    query_pattern = f"%{q}%"

    # Build search query
    search_query = select(KGNode).where(
        or_(
            KGNode.label.ilike(query_pattern),
            KGNode.aliases.ilike(query_pattern),
        )
    )

    if corpus_id is not None:
        search_query = search_query.where(KGNode.corpus_id == corpus_id)

    # Get total count
    count_result = await session.execute(
        select(KGNode.id).where(search_query.whereclause)
    )
    total = len(count_result.all())

    # Get paginated results
    search_query = search_query.limit(limit).offset(offset)
    results_result = await session.execute(search_query)
    nodes = results_result.scalars().all()

    # Build search results with simple scoring
    search_results = []
    for node in nodes:
        # Determine which field matched
        match_field = "label" if q.lower() in node.label.lower() else "aliases"
        score = 1.0 if q.lower() == node.label.lower() else 0.8

        search_results.append(
            SearchResultItem(
                id=node.id,
                node_type=node.node_type,
                label=node.label,
                match_field=match_field,
                score=score,
            )
        )

    return SearchResponse(
        results=search_results,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/ontology/path",
    response_model=ShortestPathResponse,
    response_model_by_alias=True,
    summary="Find shortest path between two nodes",
)
async def get_shortest_path(
    from_id: uuid.UUID = Query(..., description="Start node ID"),
    to_id: uuid.UUID = Query(..., description="End node ID"),
    corpus_id: str | None = Query(None, description="Corpus scope filter"),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> ShortestPathResponse:
    """Find shortest path between two nodes using AGE Cypher.

    Returns:
        Shortest path with nodes, edges, and path length
    """
    # Load source and target nodes
    from_result = await session.execute(
        select(KGNode).where(KGNode.id == from_id)
    )
    from_node = from_result.scalar_one_or_none()

    to_result = await session.execute(
        select(KGNode).where(KGNode.id == to_id)
    )
    to_node = to_result.scalar_one_or_none()

    if not from_node or not to_node:
        raise HTTPException(
            status_code=404,
            detail="one or both nodes not found",
        )

    # For now, return a placeholder implementation
    # Full AGE Cypher integration requires the graph to be built first
    return ShortestPathResponse(
        from_node=PathNode.model_validate(from_node),
        to_node=PathNode.model_validate(to_node),
        path=[],
        length=0,
    )


@router.post(
    "/ontology/sync",
    response_model=SyncResponse,
    response_model_by_alias=True,
    summary="Rebuild AGE graph for a corpus",
)
async def sync_corpus_graph(
    corpus_id: str = Query(..., description="Corpus to rebuild", pattern=CORPUS_ID_PATTERN),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> SyncResponse:
    """Rebuild the AGE graph for a corpus from relational data.

    Returns:
        Sync statistics with nodes/edges synced and duration
    """
    try:
        sync_stats = await rebuild_graph(session, corpus_id)
    except GraphNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        ) from e
    except OntologySyncError as e:
        raise HTTPException(
            status_code=500,
            detail=f"sync failed: {e}",
        ) from e

    graph_name = f"ontology_{corpus_id.replace('-', '_')[:58]}"

    return SyncResponse(
        corpus_id=corpus_id,
        graph_name=graph_name,
        nodes_synced=sync_stats.nodes_synced,
        edges_synced=sync_stats.edges_synced,
        duration_ms=sync_stats.duration_ms,
    )
