"""Properties REST API endpoints (NFM-697).

- GET  /properties             — list measurements (paginated, filtered)
- GET  /properties/{id}        — measurement with conditions + dataset
- POST /properties             — create measurement (admin / extraction pipeline)
- PATCH /properties/{id}       — update (admin)
- GET  /properties/stats       — aggregate stats (count by category, material)
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
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

router = APIRouter()


@router.get(
    "/properties", response_model=ApiResponse[PaginatedResponse[PropertyMeasurementResponse]]
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

    分页参数: page/per_page，默认 page=1 per_page=20，最大100
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


@router.get("/properties/stats")
async def get_properties_stats_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyStatsResponse]:
    """Return aggregate statistics about measurements."""
    stats = await get_measurement_stats(db)
    return ApiResponse(success=True, data=stats)


@router.get(
    "/properties/{measurement_id}", response_model=ApiResponse[PropertyMeasurementDetailResponse]
)
async def get_property_endpoint(
    measurement_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementDetailResponse]:
    """Return a single measurement with conditions and dataset."""
    detail = await get_measurement(db, measurement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Property measurement not found")
    return ApiResponse(success=True, data=detail)


@router.post(
    "/properties", response_model=ApiResponse[PropertyMeasurementResponse], status_code=201
)
async def create_property_endpoint(
    payload: PropertyMeasurementCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementResponse]:
    """Create a new property measurement."""
    result = await create_measurement(db, payload)
    return ApiResponse(success=True, data=result)


@router.patch(
    "/properties/{measurement_id}", response_model=ApiResponse[PropertyMeasurementResponse]
)
async def update_property_endpoint(
    measurement_id: UUID,
    payload: PropertyMeasurementUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyMeasurementResponse]:
    """Update an existing property measurement."""
    result = await update_measurement(db, measurement_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Property measurement not found")
    return ApiResponse(success=True, data=result)
