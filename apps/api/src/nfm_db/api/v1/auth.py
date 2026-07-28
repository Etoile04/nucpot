"""Authorization dependencies for role-based access control."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.user import (
    BlogRole,
    Permission,
    ServiceAccountScope,
    User,
)
from nfm_db.services.auth_service import (
    decode_access_token,
    is_service_token_payload,
    token_scope,
)

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get the current authenticated user from JWT token.

    Tries HttpOnly cookie first, then falls back to Authorization header.
    """
    # Try cookie first (browser clients)
    token = request.cookies.get("access_token")

    # Fall back to Authorization header (API clients)
    if not token and credentials:
        token = credentials.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        ) from None

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


async def get_current_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current active user."""
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return user


def require_blog_role(*allowed_roles: BlogRole):
    """Dependency factory for requiring specific blog roles."""

    async def check_role(
        user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        """Check if user has one of the allowed blog roles.

        **Service accounts are always rejected here.**  They have
        ``is_service_account=True`` and zero ``blog_role``; trying to
        match them against the human-only ``BlogRole`` enum would let
        them past the gate (because ``None in (ADMIN, …)`` is False and
        ``user.blog_role`` evaluates to None) or, worse, confuse the
        enforcement invariant.  We short-circuit with 403 so behavior is
        deterministic and the failure mode is loud.
        """
        if user.is_service_account:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service accounts cannot access this endpoint",
            )
        if user.blog_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {[r.value for r in allowed_roles]}",
            )
        return user

    return check_role


def require_permission(permission: Permission):
    """Dependency factory for requiring specific permissions."""

    async def check_permission(
        user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        """Check if user has the required permission.

        Service accounts are denied here too — they have no
        ``blog_role`` and therefore no ``Permission`` set.  This keeps
        the human-only permission machinery free of service-account
        leakage.
        """
        if user.is_service_account:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service accounts cannot access this endpoint",
            )
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {permission.value}",
            )
        return user

    return check_permission


# ---------------------------------------------------------------------------
# Service-account scope guard (NFM-1973 / NFM-1972 AC-1)
# ---------------------------------------------------------------------------


def _extract_bearer_token(request: Request) -> str | None:
    """Resolve the JWT bearer token from cookie or Authorization header.

    Centralized so :func:`require_service_scope` and the dual-identity
    ``require_ingest_authority`` dependency agree on extraction rules.
    """
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def require_service_scope(scope: ServiceAccountScope):
    """Dependency factory admitting only service accounts holding ``scope``.

    Endpoint usage::

        @router.post("/extraction/ingest")
        async def ingest(
            _svc: Annotated[User, Depends(require_service_scope(
                ServiceAccountScope.EXTRACTION_INGEST
            ))],
            ...
        ):
            ...

    Rules enforced (in order):

    1. Caller must be authenticated (re-uses ``get_current_user`` so a
       missing/invalid token still returns ``401``).
    2. The looked-up user must have ``is_service_account=True``.
       Human users — even admins — are denied with ``403``.  This makes
       RBAC scope *exclusive*: an admin cannot piggyback on a service
       account's privileges, and a service account cannot piggyback on
       a human's privileges.
    3. The token's ``scope`` claim must equal the requested scope.  We
       do **not** trust ``user.is_service_account`` alone because a
       forged token could impersonate a service user without holding
       the right scope.
    """

    async def check_scope(
        user: Annotated[User, Depends(get_current_active_user)],
        request: Request,
    ) -> User:
        # Resolve the raw payload via the request cookie/header path.
        token = _extract_bearer_token(request)
        payload = decode_access_token(token) if token else None

        if not is_service_token_payload(payload):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service account credentials required",
            )

        claimed = token_scope(payload)
        if claimed != scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Service account scope '{scope.value}' required, "
                    f"token has '{claimed.value if claimed else None}'"
                ),
            )

        if not user.is_service_account:
            # Belt-and-suspenders: the token says service but the DB row
            # says human.  Either the row was demoted after token
            # issuance, or someone tampered with the JWT secret.  Refuse
            # to proceed regardless.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token claims service identity but user is human",
            )

        return user

    return check_scope


# ---------------------------------------------------------------------------
# Ingest authority — admits either a human editor/admin OR a service account
# (NFM-1972 / NFM-1980 AC-5)
# ---------------------------------------------------------------------------


def require_ingest_authority():
    """Admit a service account with ``extraction:ingest`` scope OR a human editor/admin.

    The single ``/api/v1/extraction/ingest`` endpoint accepts both
    identities; the handler then branches on
    ``user.is_service_account`` to enforce AC-5's missing-corpus
    contract:

    * **Service account + missing corpus** → auto-create the corpus row.
    * **Human + missing corpus** → reject with ``400 Bad Request``.

    Humans without editor/admin role get ``403 Forbidden`` (reviewers
    cannot ingest).  Service accounts without the right scope get
    ``403 Forbidden`` with the same message ``require_service_scope``
    would have produced.  Humans impersonating a service account are
    blocked by the same belt-and-suspenders check.
    """

    async def check(
        user: Annotated[User, Depends(get_current_active_user)],
        request: Request,
    ) -> User:
        if user.is_service_account:
            token = _extract_bearer_token(request)
            payload = decode_access_token(token) if token else None
            if not is_service_token_payload(payload):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Service account credentials required",
                )
            claimed = token_scope(payload)
            if claimed != ServiceAccountScope.EXTRACTION_INGEST:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Service account scope "
                        f"'{ServiceAccountScope.EXTRACTION_INGEST.value}' "
                        f"required, token has "
                        f"'{claimed.value if claimed else None}'"
                    ),
                )
            return user

        # Human path — must be editor or admin.
        if user.blog_role not in (BlogRole.ADMIN, BlogRole.EDITOR):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires editor or admin role",
            )
        return user

    return check


# Common role dependencies
require_admin = require_blog_role(BlogRole.ADMIN)
require_editor = require_blog_role(BlogRole.ADMIN, BlogRole.EDITOR)
require_reviewer = require_blog_role(BlogRole.ADMIN, BlogRole.REVIEWER)

__all__ = [
    "get_current_active_user",
    "get_current_user",
    "require_admin",
    "require_blog_role",
    "require_editor",
    "require_ingest_authority",
    "require_permission",
    "require_reviewer",
    "require_service_scope",
]
