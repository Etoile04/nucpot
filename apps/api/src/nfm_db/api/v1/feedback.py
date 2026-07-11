"""Feedback API endpoints: public submit and admin list."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.feedback import FeedbackStatus, FeedbackType, Priority
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
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
    pagination: PaginationParams = Depends(PaginationParams),
    _limit: int | None = Query(default=None, ge=1, le=100, alias="limit", deprecated=True, description="已弃用: 请使用 per_page 参数"),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """List feedback entries with filtering and pagination (admin endpoint).

    分页参数: page/per_page，默认 page=1 per_page=20，最大100（已弃用 limit 参数）
    """
    if _limit is not None:
        pagination = PaginationParams(page=pagination.page, per_page=_limit)

    params = FeedbackListQuery(
        status=status,
        priority=priority,
        feedback_type=feedback_type,
        page=pagination.page,
        limit=pagination.per_page,
    )

    items, total = await list_feedback(session, params)
    pages = calculate_pages(total, pagination.per_page)

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=[FeedbackResponse.model_validate(item) for item in items],
            total=total,
            page=pagination.page,
            limit=pagination.per_page,
            pages=pages,
        ),
    )
