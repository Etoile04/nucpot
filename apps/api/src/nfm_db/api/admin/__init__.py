"""Admin-only health monitoring endpoints (NFM-2416).

- ``GET /health/alerts`` — structured error summary for admin monitoring.
  Requires admin role.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_admin
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import User
from nfm_db.schemas.admin_health import AdminHealthAlertsResponse
from nfm_db.services.health_alert_service import get_admin_health_summary

router = APIRouter(tags=["管理健康监控"])


@router.get(
    "/health/alerts",
    response_model=AdminHealthAlertsResponse,
    summary="管理健康告警汇总",
    description="返回活跃健康事件的聚合摘要（按类型分组），仅管理员可访问。\n\nReturns structured error summary grouped by event type. Admin-only.",
)
@limiter.exempt
async def admin_health_alerts(
    _user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminHealthAlertsResponse:
    """Return aggregated health event summary for admin monitoring.

    Groups recent events (last 24 hours) by ``event_type``, returning
    count and most-recent timestamp for each type.  Status is
    ``"degraded"`` when any events exist, ``"healthy"`` otherwise.
    """
    return await get_admin_health_summary(db)
