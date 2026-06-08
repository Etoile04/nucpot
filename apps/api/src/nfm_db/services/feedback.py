"""Feedback service: business logic and priority auto-classification."""

import math

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.feedback import Feedback, FeedbackStatus, FeedbackType, Priority
from nfm_db.schemas.feedback import FeedbackCreate, FeedbackListQuery

# Keywords that escalate bug_report priority to high
_HIGH_PRIORITY_KEYWORDS = frozenset({
    "不可用",
    "无法访问",
    "500",
    "崩溃",
    "crash",
    "down",
    "unavailable",
    "error",
})


def classify_priority(
    feedback_type: FeedbackType,
    title: str,
    description: str,
    page_url: str | None = None,
) -> Priority:
    """Auto-classify feedback priority based on type and content keywords.

    Rules from design doc:
    - bug_report: medium (escalate to high if keywords found)
    - data_correction: high
    - feature_request: low
    - usage_inquiry: medium
    """
    type_default_map: dict[FeedbackType, Priority] = {
        FeedbackType.BUG_REPORT: Priority.MEDIUM,
        FeedbackType.DATA_CORRECTION: Priority.HIGH,
        FeedbackType.FEATURE_REQUEST: Priority.LOW,
        FeedbackType.USAGE_INQUIRY: Priority.MEDIUM,
    }

    base_priority = type_default_map.get(feedback_type, Priority.MEDIUM)

    if feedback_type == FeedbackType.BUG_REPORT:
        combined_text = f"{title} {description}".lower()
        if page_url:
            combined_text = f"{combined_text} {page_url.lower()}"

        if any(keyword in combined_text for keyword in _HIGH_PRIORITY_KEYWORDS):
            return Priority.HIGH

    return base_priority


async def create_feedback(
    session: AsyncSession,
    data: FeedbackCreate,
) -> Feedback:
    """Create a new feedback entry with auto-classified priority."""
    priority = classify_priority(
        feedback_type=data.feedback_type,
        title=data.title,
        description=data.description,
        page_url=data.page_url,
    )

    feedback = Feedback(
        feedback_type=data.feedback_type,
        title=data.title,
        description=data.description,
        page_url=data.page_url,
        contact_email=data.contact_email,
        priority=priority,
        status=FeedbackStatus.OPEN,
    )

    session.add(feedback)
    await session.flush()
    await session.refresh(feedback)

    return feedback


def _build_list_query(params: FeedbackListQuery) -> Select[tuple[Feedback]]:
    """Build a filtered select query from list parameters."""
    stmt = select(Feedback)

    if params.status is not None:
        stmt = stmt.where(Feedback.status == params.status)
    if params.priority is not None:
        stmt = stmt.where(Feedback.priority == params.priority)
    if params.feedback_type is not None:
        stmt = stmt.where(Feedback.feedback_type == params.feedback_type)

    stmt = stmt.order_by(Feedback.created_at.desc())
    return stmt


async def list_feedback(
    session: AsyncSession,
    params: FeedbackListQuery,
) -> tuple[list[Feedback], int]:
    """Return a paginated list of feedback entries matching filters."""
    # Count total matching records
    count_stmt = select(func.count()).select_from(Feedback)
    if params.status is not None:
        count_stmt = count_stmt.where(Feedback.status == params.status)
    if params.priority is not None:
        count_stmt = count_stmt.where(Feedback.priority == params.priority)
    if params.feedback_type is not None:
        count_stmt = count_stmt.where(Feedback.feedback_type == params.feedback_type)

    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    # Fetch paginated results
    offset = (params.page - 1) * params.limit
    stmt = _build_list_query(params).offset(offset).limit(params.limit)

    result = await session.execute(stmt)
    items = list(result.scalars().all())

    return items, total


def calculate_pages(total: int, limit: int) -> int:
    """Calculate total number of pages."""
    return math.ceil(total / limit) if total > 0 else 0
