"""Admin-only API routers (NFM-2440, NFM-3065).

Endpoints under ``/api/admin`` require ``BlogRole.ADMIN`` via
:func:`nfm_db.api.v1.auth.require_blog_role`.
"""

from fastapi import APIRouter

from nfm_db.api.admin.backups import router as backups_router
from nfm_db.api.admin.health import router as health_router

router = APIRouter()
"""Composite admin router; ``nfm_db.main`` mounts this under ``/api/admin``."""

router.include_router(health_router)
router.include_router(backups_router)

__all__ = ["router", "backups_router", "health_router"]
