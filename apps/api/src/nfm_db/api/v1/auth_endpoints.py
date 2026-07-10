"""Authentication and user management API endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
)
from nfm_db.api.v1.auth import (
    require_admin as require_admin_dep,
)
from nfm_db.config import get_settings
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.auth import (
    ApiResponse,
    BlogRoleResponse,
    RoleAssignmentRequest,
    RoleAssignmentResponse,
    Token,
    UserCreate,
    UserResponse,
    get_all_roles,
)
from nfm_db.services.auth_service import (
    authenticate_user,
    create_access_token,
    get_password_hash,
)

router = APIRouter(prefix="/auth", tags=["认证管理"])
settings = get_settings()


@router.post("/login", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Token:
    """Authenticate user and return access token."""
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not authenticate_user(user, form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Update last login
    user.last_login = datetime.now(UTC)
    await db.commit()

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )

    return Token(access_token=access_token)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Register a new user (available during initial setup)."""
    # Check if username exists
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists",
        )

    new_user = User(
        username=user_data.username,
        email=user_data.email,
        full_name=user_data.full_name,
        hashed_password=get_password_hash(user_data.password),
        blog_role=user_data.blog_role,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return new_user


@router.get("/me", response_model=ApiResponse[UserResponse])
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
) -> ApiResponse[UserResponse]:
    """Get information about the currently authenticated user."""
    return ApiResponse(
        success=True,
        data=UserResponse.model_validate(current_user),
    )


@router.get("/roles", response_model=ApiResponse[list[BlogRoleResponse]])
async def list_roles(
    current_user: User = Depends(require_admin_dep),
) -> ApiResponse[list[BlogRoleResponse]]:
    """List all available blog roles (admin only)."""
    roles = get_all_roles()
    return ApiResponse(
        success=True,
        data=roles,
    )


@router.put(
    "/users/{user_id}/role",
    response_model=ApiResponse[RoleAssignmentResponse],
    status_code=status.HTTP_200_OK,
)
async def assign_user_role(
    user_id: uuid.UUID,
    role_request: RoleAssignmentRequest,
    current_user: User = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RoleAssignmentResponse]:
    """Assign or remove a blog role for a user (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found",
        )

    previous_role = target_user.blog_role
    target_user.blog_role = role_request.role

    await db.commit()
    await db.refresh(target_user)

    response_data = RoleAssignmentResponse(
        user_id=target_user.id,
        username=target_user.username,
        previous_role=previous_role,
        new_role=target_user.blog_role,
        updated_at=datetime.now(),
    )

    return ApiResponse(
        success=True,
        data=response_data,
    )


__all__ = ["router"]
