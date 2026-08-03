"""Admin-only API routers (NFM-2440).

Endpoints under ``/api/admin`` require ``BlogRole.ADMIN`` via
:func:`nfm_db.api.v1.auth.require_blog_role`.
"""

from nfm_db.api.admin.health import router

__all__ = ["router"]
