"""Seed API endpoints (NFM-702).

- POST /seed/batch           — trigger batch seed import
- GET  /seed/status/{id}     — real-time batch progress
- GET  /seed/quality          — aggregate quality metrics
- PATCH /seed/review/{id}     — manual review of a measurement
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
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

router = APIRouter()


@router.post("/seed/batch", response_model=ApiResponse[BatchResponse], status_code=201)
async def create_batch_endpoint(
    payload: BatchRequest,
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


@router.get("/seed/quality", response_model=ApiResponse[QualityResponse])
async def get_quality_endpoint(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[QualityResponse]:
    """Return aggregate quality metrics across all extracted measurements."""
    metrics = await seed_service.get_quality_metrics(db)
    return ApiResponse(success=True, data=metrics)


@router.patch(
    "/seed/review/{measurement_id}",
    response_model=ApiResponse[ReviewResponse],
)
async def review_measurement_endpoint(
    measurement_id: UUID,
    payload: ReviewRequest,
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
