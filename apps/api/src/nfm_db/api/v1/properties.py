"""Properties REST API endpoints (NFM-697).

- GET  /properties             — list measurements (paginated, filtered)
- GET  /properties/{id}        — measurement with conditions + dataset
- POST /properties             — create measurement (admin / extraction pipeline)
- PATCH /properties/{id}       — update (admin)
- GET  /properties/stats       — aggregate stats (count by category, material)
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.property import (
    PropertyMeasurementCreate,
    PropertyMeasurementDetailResponse,
    PropertyMeasurementResponse,
    PropertyMeasurementUpdate,
    PropertyStatsResponse,
)
from nfm_db.services.property_service import (
    create_measurement,
    get_measurement,
    get_measurement_stats,
    list_measurements,
    update_measurement,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["属性管理"])


@router.get(
    "/properties",
    response_model=ApiResponse[PaginatedResponse[PropertyMeasurementResponse]],
    summary="分页查询物性测量列表",
    description="返回分页的物性测量记录列表，支持按材料或物性类型筛选。\n\nReturn a paginated list of measurements, optionally filtered by material or property type.",
)
async def list_properties_endpoint(
    pagination: PaginationParams = Depends(PaginationParams),
    material_id: UUID | None = Query(None, description="Filter by material"),
    property_type_id: UUID | None = Query(None, description="Filter by property type"),
    sort: str = Query("created_at", pattern="^(created_at|updated_at)$"),
    order: Literal["asc", "desc"] = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[PropertyMeasurementResponse]]:
    """Return a paginated list of measurements, optionally filtered by material or property type.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    result = await list_measurements(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        sort=sort,
        order=order,
        material_id=material_id,
        property_type_id=property_type_id,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/properties/stats",
    summary="物性测量汇总统计",
    description="获取物性测量数据的汇总统计信息。\n\nReturn aggregate statistics about measurements.",
)
async def get_properties_stats_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyStatsResponse]:
    """获取物性测量数据汇总统计.

    Return aggregate statistics about measurements."""
    stats = await get_measurement_stats(db)
    return ApiResponse(success=True, data=stats)


@router.get(
    "/properties/{measurement_id}",
    response_model=ApiResponse[PropertyMeasurementDetailResponse],
    summary="获取物性测量详情",
    description="获取单条物性测量详情，包含测量条件和数据集。\n\nReturn a single measurement with conditions and dataset.",
)
async def get_property_endpoint(
    measurement_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementDetailResponse]:
    """获取单条物性测量详情（含测量条件和数据集）.

    Return a single measurement with conditions and dataset."""
    detail = await get_measurement(db, measurement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Property measurement not found")
    return ApiResponse(success=True, data=detail)


@router.post(
    "/properties",
    response_model=ApiResponse[PropertyMeasurementResponse],
    status_code=201,
    summary="创建物性测量记录",
    description="创建一条新的物性测量记录。\n\nCreate a new property measurement.",
)
async def create_property_endpoint(
    _current_user: Annotated[User, Depends(require_editor)],
    payload: PropertyMeasurementCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementResponse]:
    """创建新物性测量记录.

    Create a new property measurement."""
    result = await create_measurement(db, payload)
    return ApiResponse(success=True, data=result)


@router.patch(
    "/properties/{measurement_id}",
    response_model=ApiResponse[PropertyMeasurementResponse],
    summary="更新物性测量记录",
    description="更新已有的物性测量记录。\n\nUpdate an existing property measurement.",
)
async def update_property_endpoint(
    _current_user: Annotated[User, Depends(require_editor)],
    measurement_id: UUID,
    payload: PropertyMeasurementUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementResponse]:
    """更新已有物性测量记录.

    Update an existing property measurement."""
    result = await update_measurement(db, measurement_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Property measurement not found")
    return ApiResponse(success=True, data=result)
