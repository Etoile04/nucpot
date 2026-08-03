"""Admin-only health monitoring endpoints (NFM-2440).

- ``GET /health/alerts`` — active error summary for admin monitoring.
  Requires ``BlogRole.ADMIN``.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_blog_role
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import BlogRole, User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.health import AdminAlertsResponse
from nfm_db.services.health_alert_service import get_active_error_summary

router = APIRouter(tags=["管理健康监控"])


@router.get(
    "/health/alerts",
    response_model=ApiResponse[AdminAlertsResponse],
    summary="管理健康告警汇总",
    description=(
        "返回活跃错误 (error/critical 级健康事件) 的聚合摘要, "
        "按 event_type 与 severity 分组, 仅管理员可访问。\n\n"
        "Returns aggregated active-error summary grouped by event_type and "
        "severity. Admin-only. Errors are reported in the body — the HTTP "
        "status stays 200 whenever the query itself succeeds."
    ),
)
@limiter.exempt
async def admin_health_alerts(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
    since: datetime | None = None,
) -> ApiResponse[AdminAlertsResponse]:
    """Return the active error summary for admin monitoring.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        db: Async database session.
        since: Optional lower bound for the aggregation window
            (defaults to 24 hours ago).
    """
    result = await get_active_error_summary(db, since=since)
    return ApiResponse(success=True, data=result)
