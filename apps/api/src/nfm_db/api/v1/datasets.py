"""Dataset REST API — NFM-4159.

Adds the ``GET /api/v1/datasets/{id}`` endpoint per §5.2 option (a).
Dataset CRUD is intentionally NOT in scope for NFM-4159 — this is the
read-only attribution-aware endpoint only.  The rest of the dataset
CRUD surface will live behind a separate ticket.

The endpoint exists primarily so the frontend can assert the negative
(§7c backstop): ``attribution.status === "intact"`` for datasets that
were NOT recast-restored from ``datasets_backup_070``.  No UI affordance
is attached — the placeholder title itself is the disclosure (§4.2).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.property import DatasetWithAttributionResponse
from nfm_db.services.dataset_service import get_dataset_with_attribution

logger = logging.getLogger(__name__)

router = APIRouter(tags=["数据集管理"])


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
