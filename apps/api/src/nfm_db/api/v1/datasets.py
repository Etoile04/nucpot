"""Dataset REST API — NFM-4159 + NFM-4991 (P2 /datasets block).

* NFM-4159 — ``GET /api/v1/datasets/{id}`` (single dataset, §5.2 attribution
  block). Dataset CRUD is intentionally NOT in scope for NFM-4159 — that
  surface lives behind a separate ticket.

* NFM-4991 — ``GET /api/v1/datasets`` (paginated list, lightweight projection).
  The list deliberately omits ``description`` and joins ``material`` /
  ``source`` only when ``?expand=material`` or ``?expand=source`` is
  supplied, so the default anonymous-list query stays a single-table scan
  and doesn't drag in eager loads the /datasets page never uses.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.property import (
    DatasetListItem,
    DatasetWithAttributionResponse,
)
from nfm_db.services.dataset_service import (
    get_dataset_with_attribution,
    list_datasets,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["数据集管理"])


# IMPORTANT: list route MUST be registered BEFORE the ``{dataset_id}``
# path-param route, otherwise FastAPI matches ``/datasets/{dataset_id}``
# greedily and ``/datasets`` is shadowed (it 404s with the standard
# "dataset not found" since UUID parsing rejects the empty path segment).


@router.get(
    "/datasets",
    response_model=ApiResponse[PaginatedResponse[DatasetListItem]],
    summary="数据集分页列表（NFM-4991）",
    description=(
        "返回数据集分页列表。可选过滤:``material_id``、``source_id``、"
        "``is_verified``。``expand`` 支持 ``material``（返回 material_name）"
        "和 ``source``（返回 source_title），多个值用逗号分隔。"
        "默认按 ``updated_at`` 降序，匿名访问可用。\n\n"
        "分页契约 (NFM-4308 ③): ``page_size`` 为 ``per_page`` 别名；"
        "默认 20，上限 100。超限值按 100 执行并在 ``data.truncated`` "
        "回传 ``true``。"
    ),
)
async def list_datasets_endpoint(
    db: AsyncSession = Depends(get_db),
    pagination: PaginationParams = Depends(PaginationParams),
    material_id: UUID | None = Query(default=None, description="按材料过滤"),
    source_id: UUID | None = Query(default=None, description="按数据源过滤"),
    is_verified: bool | None = Query(default=None, description="是否已审核"),
    expand: str | None = Query(
        default=None,
        description="可选扩展:material,source(逗号分隔)",
    ),
) -> ApiResponse[PaginatedResponse[DatasetListItem]]:
    """Paginated dataset list — see module docstring."""
    expand_set = (
        {token.strip() for token in expand.split(",") if token.strip()}
        if expand
        else frozenset()
    )
    page_result = await list_datasets(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        material_id=material_id,
        source_id=source_id,
        is_verified=is_verified,
        expand_material="material" in expand_set,
        expand_source="source" in expand_set,
    )
    # NFM-4308 ③ — echo the truncated flag from the PaginationParams
    # clamp so callers know they were capped. The service-level
    # defence-in-depth clamp also flips this in case the service is
    # called outside the route layer.
    if pagination.truncated and not page_result.truncated:
        page_result = page_result.model_copy(update={"truncated": True})
    return ApiResponse(success=True, data=page_result)


@router.get(
    "/datasets/{dataset_id}",
    response_model=ApiResponse[DatasetWithAttributionResponse],
    summary="按 ID 获取数据集（含 §5.2 attribution 块）",
    description=(
        "返回单条数据集并附带 §5.2 LOCKED 合同 ``attribution`` 块 "
        "(status ∈ ``{'placeholder', 'intact'}``)。\n\n"
        "``placeholder`` 在 10 recast-restored 数据集上命中；其他均返回 "
        "``intact``。This endpoint exists so the frontend can assert the "
        "negative; no UI affordance is attached to the field."
    ),
)
async def get_dataset_endpoint(
    dataset_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DatasetWithAttributionResponse]:
    """Return a single dataset with its §5.2 attribution block, or 404."""
    dataset = await get_dataset_with_attribution(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ApiResponse(success=True, data=dataset)


__all__ = ["router"]
