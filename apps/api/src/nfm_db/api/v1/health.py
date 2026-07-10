"""Health check endpoint."""

from fastapi import APIRouter

from nfm_db.middleware.rate_limit import limiter

router = APIRouter(tags=["健康检查"])


@router.get("/health")
@limiter.exempt
async def health_check() -> dict[str, str]:
    """返回API服务健康状态.

    Return API health status."""
    return {"status": "ok"}
