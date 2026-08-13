"""Admin health alerts endpoint (NFM-2414).

Provides a structured error summary for admin monitoring.
Requires admin role authentication and queries the ``health_events``
table populated by :mod:`nfm_db.services.health_event_emitter`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_admin
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.admin_health import HealthAlertsResponse
from nfm_db.schemas.common import ApiResponse
from nfm_db.services.admin_health_service import get_health_alerts

router = APIRouter(prefix="/admin", tags=["管理监控"])


@router.get(
    "/health/alerts",
    response_model=ApiResponse[HealthAlertsResponse],
    summary="健康告警摘要",
    description="返回系统错误分类汇总，供管理员监控使用。需要管理员权限。\n\nReturns structured error summary for admin monitoring. Requires admin role.",
)
async def admin_health_alerts(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[HealthAlertsResponse]:
    """Return structured health alert summary with error counts by category.

    Protected by admin role requirement. Aggregates ``health_events``
    rows in the last 24 hours by ``event_type`` and reports the
    per-type count and most recent timestamp. When no events are
    present the response is ``status="healthy"``; any aggregation
    flips it to ``"degraded"``.
    """
    result = await get_health_alerts(db)
    return ApiResponse(success=True, data=result)
