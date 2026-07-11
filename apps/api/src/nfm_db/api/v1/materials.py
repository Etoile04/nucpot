"""Materials REST API endpoints (NFM-696).

- GET  /materials              — paginated, filtered list
- GET  /materials/search       — full-text search by name/alias/formula
- GET  /materials/{id}         — detail with aliases + composition
- POST /materials              — create (admin)
- POST /materials/batch-import — bulk CSV/JSON import (NFM-1141)
- PATCH /materials/{id}        — update (admin)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.material import (
    BatchImportResult,
    MaterialCreate,
    MaterialDetailResponse,
    MaterialResponse,
    MaterialUpdate,
)
from nfm_db.services.batch_import_service import (
    BATCH_IMPORT_MAX_SIZE_MB,
    batch_import_materials,
    get_import_lock,
)
from nfm_db.services.material_service import (
    create_material,
    get_material,
    list_materials,
    search_materials,
    update_material,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["材料管理"])


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
    """获取单个材料详情（含别名和成分）.

    Return a single material with aliases and composition."""
    detail = await get_material(db, material_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return ApiResponse(success=True, data=detail)


@router.post("/materials", response_model=ApiResponse[MaterialResponse], status_code=201)
async def create_material_endpoint(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialResponse]:
    """创建新材料.

    Create a new material."""
    result = await create_material(db, payload)
    return ApiResponse(success=True, data=result)


@router.patch("/materials/{material_id}", response_model=ApiResponse[MaterialResponse])
async def update_material_endpoint(
    material_id: UUID,
    payload: MaterialUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialResponse]:
    """更新已有材料信息.

    Update an existing material."""
    result = await update_material(db, material_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return ApiResponse(success=True, data=result)


@router.post(
    "/materials/batch-import",
    response_model=ApiResponse[BatchImportResult],
    status_code=200,
)
@limiter.exempt
async def batch_import_endpoint(
    request: Request,
    file: UploadFile = File(..., description="CSV or JSON file"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BatchImportResult]:
    """批量导入材料数据（支持 CSV 和 JSON 文件）.

    Bulk-import materials from a CSV or JSON file. Valid rows are
    upserted (matched by name+formula); invalid rows are reported
    in the response ``errors`` list.
    """
    filename = file.filename or "unknown.csv"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ("csv", "json"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Use .csv or .json",
        )

    content = await file.read()
    max_bytes = BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds {BATCH_IMPORT_MAX_SIZE_MB}MB limit",
        )

    # Per-IP concurrency control: non-blocking acquire, fail fast if busy
    client_ip = request.client.host if request.client else "unknown"
    lock = await get_import_lock(client_ip)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.001)
    except TimeoutError:
        raise HTTPException(
            status_code=409,
            detail="A batch import is already in progress for this client. "
            "Please wait for it to finish.",
        )

    try:
        result = await batch_import_materials(
            db,
            content=content,
            filename=filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        lock.release()

    return ApiResponse(success=True, data=result)
