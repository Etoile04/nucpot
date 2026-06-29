"""Authentication and authorization middleware for RBAC."""

from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from nfm_db.models import BlogRole, Permission, User


class UnauthorizedError(HTTPException):
    """Raised when authentication fails."""

    def __init__(self, detail: str = "Could not validate credentials") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(HTTPException):
    """Raised when authorization fails (insufficient permissions)."""

    def __init__(self, detail: str = "Insufficient permissions") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


class AuthenticationError(HTTPException):
    """Raised when user account is inactive or invalid."""

    def __init__(self, detail: str = "User account is inactive") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
) -> User:
    """Get the current authenticated user from the Authorization header.

    This is a simplified version that would normally:
    1. Decode and verify the JWT token
    2. Look up the user from the database
    3. Return the user object

    For now, this is a placeholder that should be replaced with actual JWT validation.

    Args:
        credentials: The HTTP authorization credentials from the request header

    Returns:
        The authenticated User object

    Raises:
        UnauthorizedError: If no credentials provided or token is invalid
        AuthenticationError: If user account is inactive
    """
    if not credentials:
        raise UnauthorizedError("No authentication credentials provided")

    # TODO: Implement actual JWT validation here
    # For now, this is a placeholder that would:
    # 1. Decode the JWT token from credentials.credentials
    # 2. Verify the token signature and expiration
    # 3. Extract the user_id from the token payload
    # 4. Query the database for the user

    # Placeholder: raise error until real auth is implemented
    raise UnauthorizedError("JWT authentication not yet implemented")


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current active user.

    This dependency ensures the user is authenticated and their account is active.

    Args:
        current_user: The current authenticated user

    Returns:
        The active User object

    Raises:
        AuthenticationError: If user account is inactive
    """
    if not current_user.is_active:
        raise AuthenticationError("User account is inactive")

    return current_user


class RequirePermission:
    """Dependency factory for requiring specific permissions."""

    def __init__(self, permission: Permission) -> None:
        """Initialize the permission requirement.

        Args:
            permission: The required permission
        """
        self.permission = permission

    async def __call__(
        self,
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        """Check if the current user has the required permission.

        Args:
            current_user: The current authenticated active user

        Returns:
            The user object if they have the required permission

        Raises:
            ForbiddenError: If user lacks the required permission
        """
        if not current_user.has_permission(self.permission):
            raise ForbiddenError(
                f"User lacks required permission: {self.permission.value}"
            )

        return current_user


class RequireRole:
    """Dependency factory for requiring specific blog roles."""

    def __init__(self, *roles: BlogRole) -> None:
        """Initialize the role requirement.

        Args:
            *roles: One or more required roles (user must have at least one)
        """
        self.roles = roles

    async def __call__(
        self,
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        """Check if the current user has one of the required roles.

        Args:
            current_user: The current authenticated active user

        Returns:
            The user object if they have one of the required roles

        Raises:
            ForbiddenError: If user lacks any of the required roles
        """
        if not current_user.blog_role:
            raise ForbiddenError("User has no blog role assigned")

        if current_user.blog_role not in self.roles:
            raise ForbiddenError(
                f"User role {current_user.blog_role.value} not in required roles: "
                f"{[r.value for r in self.roles]}"
            )

        return current_user


# Convenience dependency factories
require_admin = Annotated[User, Depends(RequireRole(BlogRole.ADMIN))]
require_editor = Annotated[User, Depends(RequireRole(BlogRole.EDITOR))]
require_reviewer = Annotated[User, Depends(RequireRole(BlogRole.REVIEWER))]
require_admin_or_reviewer = Annotated[
    User,
    Depends(RequireRole(BlogRole.ADMIN, BlogRole.REVIEWER)),
]

# Permission-based dependencies
require_create_post = Annotated[User, Depends(RequirePermission(Permission.CREATE_POST))]
require_edit_post = Annotated[User, Depends(RequirePermission(Permission.EDIT_POST))]
require_delete_post = Annotated[User, Depends(RequirePermission(Permission.DELETE_POST))]
require_publish_post = Annotated[User, Depends(RequirePermission(Permission.PUBLISH_POST))]
require_review_post = Annotated[User, Depends(RequirePermission(Permission.REVIEW_POST))]
require_assign_roles = Annotated[User, Depends(RequirePermission(Permission.ASSIGN_ROLES))]
