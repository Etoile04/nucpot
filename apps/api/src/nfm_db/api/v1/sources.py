"""Data Sources REST API endpoints (NFM-698).

- GET  /sources              — paginated, filtered list
- GET  /sources/{id}         — detail with authors (ordered by author_order)
- POST /sources              — create (admin / extraction pipeline)
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.source import (
    DataSourceCreate,
    DataSourceDetailResponse,
    DataSourceResponse,
)
from nfm_db.services.source_service import (
    create_source,
    get_source,
    list_sources,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sources", response_model=ApiResponse[PaginatedResponse[DataSourceResponse]])
async def list_sources_endpoint(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    year: int | None = Query(None, description="Filter by publication year"),
    source_type: str | None = Query(None, description="Filter by source type"),
    sort: str = Query("created_at", pattern="^(created_at|updated_at|title|year)$"),
    order: Literal["asc", "desc"] = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[DataSourceResponse]]:
    """Return a paginated, filtered list of data sources."""
    result = await list_sources(
        db,
        year=year,
        source_type=source_type,
        page=page,
        per_page=per_page,
        sort=sort,
        order=order,
    )
    return ApiResponse(success=True, data=result)


@router.get("/sources/{source_id}", response_model=ApiResponse[DataSourceDetailResponse])
async def get_source_endpoint(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DataSourceDetailResponse]:
    """Return a single source with authors ordered by author_order."""
    detail = await get_source(db, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return ApiResponse(success=True, data=detail)


@router.post("/sources", response_model=ApiResponse[DataSourceResponse], status_code=201)
async def create_source_endpoint(
    payload: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DataSourceResponse]:
    """Create a new data source."""
    result = await create_source(db, payload)
    return ApiResponse(success=True, data=result)
