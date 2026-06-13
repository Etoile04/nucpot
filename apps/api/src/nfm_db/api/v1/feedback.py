"""Feedback API endpoints: public submit and admin list."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.feedback import FeedbackStatus, FeedbackType, Priority
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.feedback import (
    FeedbackCreate,
    FeedbackCreateResult,
    FeedbackListQuery,
    FeedbackResponse,
)
from nfm_db.services.feedback import calculate_pages, create_feedback, list_feedback

router = APIRouter()


@router.post("/feedback", response_model=ApiResponse, status_code=201)
async def submit_feedback(
    payload: FeedbackCreate,
    session: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Submit user feedback (public endpoint)."""
    feedback = await create_feedback(session, payload)

    return ApiResponse(
        success=True,
        data=FeedbackCreateResult.model_validate(feedback),
    )


@router.get("/feedback", response_model=ApiResponse)
async def list_feedback_endpoint(
    status: FeedbackStatus | None = Query(default=None),
    priority: Priority | None = Query(default=None),
    feedback_type: FeedbackType | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """List feedback entries with filtering and pagination (admin endpoint)."""
    params = FeedbackListQuery(
        status=status,
        priority=priority,
        feedback_type=feedback_type,
        page=page,
        limit=limit,
    )

    items, total = await list_feedback(session, params)
    pages = calculate_pages(total, params.limit)

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=[FeedbackResponse.model_validate(item) for item in items],
            total=total,
            page=params.page,
            limit=params.limit,
            pages=pages,
        ),
    )
