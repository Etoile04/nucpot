"""Seed API endpoints (NFM-702).

- POST /seed/batch           — trigger batch seed import
- GET  /seed/status/{id}     — real-time batch progress
- GET  /seed/quality          — aggregate quality metrics
- PATCH /seed/review/{id}     — manual review of a measurement
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.seed import (
    BatchRequest,
    BatchResponse,
    QualityResponse,
    ReviewRequest,
    ReviewResponse,
)
from nfm_db.services import seed_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["种子数据"])


@router.post(
    "/seed/batch",
    response_model=ApiResponse[BatchResponse],
    status_code=201,
    summary="触发批量种子导入",
    description="根据DOI列表触发批量种子数据导入。\n\nTrigger a batch seed import for the given DOIs.",
)
async def create_batch_endpoint(
    payload: BatchRequest,
    current_user: Annotated[User, Depends(require_editor)],
) -> ApiResponse[BatchResponse]:
    """Trigger a batch seed import for the given DOIs."""
    batch_id = await seed_service.start_batch(payload.dois)
    result = BatchResponse(
        batch_id=batch_id,
        total=len(payload.dois),
        message="Batch started",
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/seed/status/{batch_id}",
    summary="查询批量导入进度",
    description="返回批量导入任务的实时进度。\n\nReturn real-time progress for a batch import job.",
)
async def get_batch_status_endpoint(
    batch_id: str,
) -> dict[str, object]:
    """Return real-time progress for a batch import job."""
    progress = seed_service.get_batch_status(batch_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    # dataclass is not a Pydantic model; expose as dict in ApiResponse
    return {
        "success": True,
        "data": {
            "batch_id": progress.batch_id,
            "total": progress.total,
            "completed": progress.completed,
            "failed": progress.failed,
            "in_progress": progress.in_progress,
            "errors": progress.errors,
        },
    }


@router.get(
    "/seed/quality",
    response_model=ApiResponse[QualityResponse],
    summary="获取种子数据质量指标",
    description="返回所有已提取测量数据的聚合质量指标。\n\nReturn aggregate quality metrics across all extracted measurements.",
)
async def get_quality_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[QualityResponse]:
    """Return aggregate quality metrics across all extracted measurements."""
    metrics = await seed_service.get_quality_metrics(db)
    return ApiResponse(success=True, data=metrics)


@router.patch(
    "/seed/review/{measurement_id}",
    response_model=ApiResponse[ReviewResponse],
    summary="审核测量数据",
    description="更新属性测量数据的审核状态。\n\nUpdate the review status of a property measurement.",
)
async def review_measurement_endpoint(
    measurement_id: UUID,
    payload: ReviewRequest,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewResponse]:
    """Update the review status of a property measurement."""
    result = await seed_service.review_measurement(
        db,
        measurement_id,
        review_status=payload.review_status,
        reviewer_note=payload.reviewer_note,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Property measurement not found")
    return ApiResponse(success=True, data=result)
