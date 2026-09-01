"""Materials REST API endpoints (NFM-696).

- GET  /materials                       — paginated, filtered list
- GET  /materials/search                — full-text search by name/alias/formula
- GET  /materials/{id}                  — detail with aliases + composition
- GET  /materials/{id}/properties       — property table (NFM-1067)
- POST /materials                       — create (admin)
- POST /materials/batch-import          — bulk CSV/JSON import (NFM-1141)
- PATCH /materials/{id}                 — update (admin)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user, require_editor
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.material import (
    BatchImportResult,
    MaterialCategoryListResponse,
    MaterialCreate,
    MaterialDetailResponse,
    MaterialResponse,
    MaterialUpdate,
    UncategorizedCountResponse,
)
from nfm_db.schemas.property import MaterialPropertyListResponse
from nfm_db.services.batch_import_service import (
    BATCH_IMPORT_MAX_SIZE_MB,
    batch_import_materials,
    get_import_lock,
)
from nfm_db.services.material_service import (
    count_uncategorized_materials,
    create_material,
    get_material,
    list_material_categories,
    list_materials,
    search_materials,
    update_material,
)
from nfm_db.services.property_service import list_material_properties

logger = logging.getLogger(__name__)

router = APIRouter(tags=["材料管理"])


@router.get(
    "/materials",
    response_model=ApiResponse[PaginatedResponse[MaterialResponse]],
    summary="分页查询材料列表",
    description="返回分页的材料列表，支持按类别筛选和排序。\n\nReturn a paginated list of materials, optionally filtered by category.",
)
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


@router.get(
    "/materials/search",
    response_model=ApiResponse[PaginatedResponse[MaterialResponse]],
    summary="搜索材料",
    description="按材料名称、化学式或别名进行模糊搜索。\n\nSearch materials by name, formula, or alias (ILIKE). Optionally filter by category (NFM-3917 / Tier 1D).",
)
async def search_materials_endpoint(
    q: str = Query("", description="Search query"),
    pagination: PaginationParams = Depends(PaginationParams),
    category_id: UUID | None = Query(
        None,
        description="Optional category filter (NFM-3917 / Tier 1D). Composes with the search query.",
    ),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[MaterialResponse]]:
    """Search materials by name, formula, or alias (ILIKE).

    Optional ``category_id`` composes with the search query — search and
    category filter are NOT mutually exclusive (NFM-3917 / Tier 1D CPO
    decision; mutual exclusion would silently drop the user's category
    selection when they type a query).

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    result = await search_materials(
        db,
        query=q,
        page=pagination.page,
        limit=pagination.per_page,
        category_id=category_id,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/material-categories",
    response_model=ApiResponse[MaterialCategoryListResponse],
    summary="材料类别列表",
    description="返回所有材料类别（用于 /materials 页面的类别下拉筛选，NFM-3917 / Tier 1D）。\n\nList every material category (ordered for the /materials category dropdown).",
)
async def list_material_categories_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialCategoryListResponse]:
    """List every material category for the /materials filter dropdown.

    NFM-3917 / Tier 1D: read-only, public, no auth. Sorted by
    ``(sort_order ASC, name ASC)`` so the seeded taxonomy renders in
    curator-chosen order.
    """
    result = await list_material_categories(db)
    return ApiResponse(success=True, data=result)


@router.get(
    "/materials/uncategorized-count",
    response_model=ApiResponse[UncategorizedCountResponse],
    summary="未分类材料数量 (NFM-4030)",
    description="返回 category_id IS NULL 的材料数量,用于 /materials 页面静默数据缺口徽章。\n\nCount of materials with `category_id IS NULL`. Powers the silent-data-gap badge on `/materials` so users see that ~47/135 rows are unreachable under any category dropdown selection.",
)
async def count_uncategorized_materials_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UncategorizedCountResponse]:
    """Return the count of materials with no category (NFM-4030).

    Public, read-only — no auth — same posture as the categories
    dropdown so the badge renders for anonymous viewers.
    """
    count = await count_uncategorized_materials(db)
    return ApiResponse(success=True, data=UncategorizedCountResponse(count=count))


@router.get(
    "/materials/{material_id}",
    response_model=ApiResponse[MaterialDetailResponse],
    summary="获取材料详情",
    description="获取单个材料的详细信息，包含别名和成分数据。\n\nReturn a single material with aliases and composition.",
)
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


@router.post(
    "/materials",
    response_model=ApiResponse[MaterialResponse],
    status_code=201,
    summary="创建材料",
    description="创建一条新的材料记录。\n\nCreate a new material.",
)
async def create_material_endpoint(
    payload: MaterialCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialResponse]:
    """创建新材料.

    Create a new material."""
    result = await create_material(db, payload)
    return ApiResponse(success=True, data=result)


@router.patch(
    "/materials/{material_id}",
    response_model=ApiResponse[MaterialResponse],
    summary="更新材料",
    description="更新已有材料的信息字段。\n\nUpdate an existing material.",
)
async def update_material_endpoint(
    material_id: UUID,
    payload: MaterialUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
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
    summary="批量导入材料",
    description="通过 CSV 或 JSON 文件批量导入材料数据，有效行按名称+化学式匹配进行 upsert，无效行在响应中报告错误。\n\nBulk-import materials from a CSV or JSON file. Valid rows are upserted (matched by name+formula); invalid rows are reported in the response errors list.",
)
@limiter.exempt
async def batch_import_endpoint(
    request: Request,
    current_user: Annotated[User, Depends(require_editor)],
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


# ---------------------------------------------------------------------------
# Per-material property table (NFM-1067)
# ---------------------------------------------------------------------------


@router.get(
    "/materials/{material_id}/properties",
    response_model=ApiResponse[MaterialPropertyListResponse],
)
async def list_material_properties_endpoint(
    material_id: UUID,
    page: int = Query(1, ge=1, description="1-based page index"),
    limit: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
    sort: str = Query("name", pattern="^(name|value|created_at)$"),
    order: Literal["asc", "desc"] = Query("asc"),
    filter: str | None = Query(None, description="Case-insensitive name substring"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MaterialPropertyListResponse]:
    """List a single material's property table (NFM-1067).

    Returns a flat list of ``{id, name, value, unit, source, confidence}``
    rows shaped for the React property table. ``404`` when the material
    does not exist; ``200`` with an empty list when the material exists
    but has no measurements.
    """
    result = await list_material_properties(
        db,
        material_id,
        page=page,
        limit=limit,
        sort=sort,
        order=order,
        filter=filter,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return ApiResponse(success=True, data=result)
