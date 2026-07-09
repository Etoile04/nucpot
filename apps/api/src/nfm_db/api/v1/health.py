"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["健康检查"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """获取API健康状态。"""
    return {"status": "ok"}
