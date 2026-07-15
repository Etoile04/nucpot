"""Data Sources REST API endpoints (NFM-698).

- GET  /sources              — paginated, filtered list
- GET  /sources/{id}         — detail with authors (ordered by author_order)
- POST /sources              — create (admin / extraction pipeline)
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
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

router = APIRouter(tags=["数据源管理"])


@router.get("/sources", response_model=ApiResponse[PaginatedResponse[DataSourceResponse]])
async def list_sources_endpoint(
    pagination: PaginationParams = Depends(PaginationParams),
    year: int | None = Query(None, description="Filter by publication year"),
    source_type: str | None = Query(None, description="Filter by source type"),
    sort: str = Query("created_at", pattern="^(created_at|updated_at|title|year)$"),
    order: Literal["asc", "desc"] = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[DataSourceResponse]]:
    """Return a paginated, filtered list of data sources.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    result = await list_sources(
        db,
        year=year,
        source_type=source_type,
        page=pagination.page,
        per_page=pagination.per_page,
        sort=sort,
        order=order,
    )
    return ApiResponse(success=True, data=result)


@router.get("/sources/{source_id}", response_model=ApiResponse[DataSourceDetailResponse])
async def get_source_endpoint(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DataSourceDetailResponse]:
    """获取单个数据源详情（含作者列表）.

    Return a single source with authors ordered by author_order."""
    detail = await get_source(db, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return ApiResponse(success=True, data=detail)


@router.post("/sources", response_model=ApiResponse[DataSourceResponse], status_code=201)
async def create_source_endpoint(
    payload: DataSourceCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DataSourceResponse]:
    """创建新数据源.

    Create a new data source."""
    result = await create_source(db, payload)
    return ApiResponse(success=True, data=result)
