"""Materials REST API endpoints (NFM-696).

- GET  /materials              — paginated, filtered list
- GET  /materials/search       — full-text search by name/alias/formula
- GET  /materials/{id}         — detail with aliases + composition
- POST /materials              — create (admin)
- PATCH /materials/{id}        — update (admin)
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.material import (
    MaterialCreate,
    MaterialDetailResponse,
    MaterialResponse,
    MaterialUpdate,
)
from nfm_db.services.material_service import (
    create_material,
    get_material,
    list_materials,
    search_materials,
    update_material,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/materials", response_model=ApiResponse[PaginatedResponse[MaterialResponse]])
async def list_materials_endpoint(
    pagination: PaginationParams = Depends(PaginationParams),
    category_id: UUID | None = Query(None, description="Filter by category"),
    sort: str = Query("created_at", pattern="^(name|created_at|updated_at)$"),
    order: Literal["asc", "desc"] = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[MaterialResponse]]:
    """Return a paginated list of materials, optionally filtered by category.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    result = await list_materials(
        db,
        page=pagination.page,
        limit=pagination.per_page,
        sort=sort,
        order=order,
        category_id=category_id,
    )
    return ApiResponse(success=True, data=result)


@router.get("/materials/search", response_model=ApiResponse[PaginatedResponse[MaterialResponse]])
async def search_materials_endpoint(
    q: str = Query("", description="Search query"),
    pagination: PaginationParams = Depends(PaginationParams),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[MaterialResponse]]:
    """Search materials by name, formula, or alias (ILIKE).

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    result = await search_materials(
        db,
        query=q,
        page=pagination.page,
        limit=pagination.per_page,
    )
    return ApiResponse(success=True, data=result)


@router.get("/materials/{material_id}", response_model=ApiResponse[MaterialDetailResponse])
async def get_material_endpoint(
    material_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialDetailResponse]:
    """Return a single material with aliases and composition."""
    detail = await get_material(db, material_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return ApiResponse(success=True, data=detail)


@router.post("/materials", response_model=ApiResponse[MaterialResponse], status_code=201)
async def create_material_endpoint(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialResponse]:
    """Create a new material."""
    result = await create_material(db, payload)
    return ApiResponse(success=True, data=result)


@router.patch("/materials/{material_id}", response_model=ApiResponse[MaterialResponse])
async def update_material_endpoint(
    material_id: UUID,
    payload: MaterialUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialResponse]:
    """Update an existing material."""
    result = await update_material(db, material_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return ApiResponse(success=True, data=result)
