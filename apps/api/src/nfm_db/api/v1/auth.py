"""Authorization dependencies for role-based access control."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.user import BlogRole, Permission, User
from nfm_db.services.auth_service import decode_access_token

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get the current authenticated user from JWT token."""
    token = credentials.credentials
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
        """Check if user has one of the allowed blog roles."""
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
        """Check if user has the required permission."""
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {permission.value}",
            )
        return user

    return check_permission


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
    "require_permission",
    "require_reviewer",
]
