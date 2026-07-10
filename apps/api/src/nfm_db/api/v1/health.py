"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["健康检查"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return API health status."""
    return {"status": "ok"}
