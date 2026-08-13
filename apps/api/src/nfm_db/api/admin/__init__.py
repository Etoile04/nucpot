"""Admin-only API routers (NFM-2440, NFM-3065).

Endpoints under ``/api/admin`` require ``BlogRole.ADMIN`` via
:func:`nfm_db.api.v1.auth.require_blog_role`.
"""

from nfm_db.api.admin.backups import router as backups_router
from nfm_db.api.admin.health import router as health_router

router = health_router
"""Default import for backwards compatibility (existed pre-NFM-3065)."""

# NFM-3065: merge both admin routers into a single composite router that
# ``nfm_db.main`` mounts under ``/api/admin``.  Health endpoints keep their
# pre-existing paths; backup endpoints register ``/backups`` and
# ``/backups/stats`` alongside them.
router.include_router(backups_router)

__all__ = ["router", "backups_router", "health_router"]
