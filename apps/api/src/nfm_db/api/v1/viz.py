"""Visualization API endpoints for NVL data."""

from fastapi import APIRouter, Query

from nfm_db.schemas.viz import NvlResponse, VizStatsResponse
from nfm_db.services.ontology_service import get_nvl_data, get_viz_stats

router = APIRouter(tags=["可视化"])


@router.get("/viz/nvl", response_model=NvlResponse)
async def get_nvl(
    class_filter: str | None = Query(None, alias="class", description="Filter by class subtree"),
    search: str | None = Query(None, description="Filter by search term"),
    max_nodes: int | None = Query(None, ge=1, description="Maximum number of nodes"),
) -> NvlResponse:
    """Get NVL visualization data with optional filtering.

    Args:
        class_filter: Filter nodes to those containing this class
        search: Filter nodes to those containing this term in name
        max_nodes: Limit total nodes returned

    Returns:
        NvlResponse with nodes and relationships
    """
    return await get_nvl_data(
        class_filter=class_filter,
        search_term=search,
        max_nodes=max_nodes,
    )


@router.get("/viz/stats", response_model=VizStatsResponse)
async def get_stats() -> VizStatsResponse:
    """Get ontology statistics.

    Returns:
        VizStatsResponse with node/relationship counts and class distribution
    """
    return await get_viz_stats()
