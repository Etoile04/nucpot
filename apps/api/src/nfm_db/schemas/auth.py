"""Authentication and authorization schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, ConfigDict
import uuid

from nfm_db.models.user import BlogRole, Permission
from nfm_db.schemas.common import ApiResponse


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token payload data."""

    user_id: str | None = None


class LoginRequest(BaseModel):
    """Login request schema."""

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class UserBase(BaseModel):
    """Base user schema."""

    username: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    """User creation schema."""

    password: str = Field(..., min_length=8)
    blog_role: BlogRole | None = None


class UserUpdate(BaseModel):
    """User update schema."""

    email: EmailStr | None = None
    full_name: str | None = None
    blog_role: BlogRole | None = None
    is_active: bool | None = None


class UserResponse(UserBase):
    """User response schema."""

    id: uuid.UUID
    blog_role: BlogRole | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None

    model_config = ConfigDict(from_attributes=True)


class RoleAssignmentRequest(BaseModel):
    """Role assignment request schema."""

    role: BlogRole | None = Field(
        description="Role to assign (null to remove role)",
        default=None,
    )


class RoleAssignmentResponse(BaseModel):
    """Response model for role assignment confirmation."""

    user_id: uuid.UUID
    username: str
    previous_role: BlogRole | None
    new_role: BlogRole | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BlogRoleResponse(BaseModel):
    """Response model for a single blog role."""

    role: BlogRole
    display_name: str
    description: str
    permissions: list[Permission]

    model_config = ConfigDict(from_attributes=True)


# Predefined role information
ROLE_INFO = {
    BlogRole.ADMIN: {
        "display_name": "Administrator",
        "description": "Full access to blog management and role assignment",
        "permissions": [
            Permission.CREATE_POST,
            Permission.EDIT_POST,
            Permission.DELETE_POST,
            Permission.PUBLISH_POST,
            Permission.REVIEW_POST,
            Permission.ASSIGN_ROLES,
        ],
    },
    BlogRole.EDITOR: {
        "display_name": "Editor",
        "description": "Can create and edit blog posts",
        "permissions": [
            Permission.CREATE_POST,
            Permission.EDIT_POST,
        ],
    },
    BlogRole.REVIEWER: {
        "display_name": "Reviewer",
        "description": "Can review and approve blog posts",
        "permissions": [
            Permission.REVIEW_POST,
        ],
    },
}


def get_role_info(role: BlogRole) -> BlogRoleResponse:
    """Get detailed information about a specific role.

    Args:
        role: The blog role to get information for

    Returns:
        BlogRoleResponse with role details and permissions
    """
    info = ROLE_INFO[role]
    return BlogRoleResponse(
        role=role,
        display_name=info["display_name"],
        description=info["description"],
        permissions=info["permissions"],
    )


def get_all_roles() -> list[BlogRoleResponse]:
    """Get information about all available blog roles.

    Returns:
        List of BlogRoleResponse objects for all roles
    """
    return [get_role_info(role) for role in BlogRole]


__all__ = [
    "Token",
    "TokenData",
    "LoginRequest",
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "RoleAssignmentRequest",
    "RoleAssignmentResponse",
    "BlogRoleResponse",
    "get_role_info",
    "get_all_roles",
    "ROLE_INFO",
]
