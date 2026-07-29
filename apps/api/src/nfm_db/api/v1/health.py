"""Health check endpoint."""

from fastapi import APIRouter

from monitoring.worker_health import worker_health
from nfm_db.middleware.rate_limit import limiter

router = APIRouter(tags=["健康检查"])


@router.get("/health", summary="健康检查", description="返回API服务健康状态，用于负载均衡探针和监控告警。\n\nReturns API health status for load balancer probes and monitoring alerts.")
@limiter.exempt
async def health_check() -> dict:
    """返回API服务健康状态.

    Includes worker consecutive-failure counter (NFM-2014)."""
    return worker_health.snapshot()
