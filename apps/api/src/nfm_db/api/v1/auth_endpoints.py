"""Authentication and user management API endpoints."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import (
    get_current_active_user,
    get_current_user,
)
from nfm_db.api.v1.auth import (
    require_admin as require_admin_dep,
)
from nfm_db.config import get_settings
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import (
    ServiceAccountScope,
    User,
)
from nfm_db.schemas.auth import (
    ApiResponse,
    BlogRoleResponse,
    RefreshTokenResponse,
    RoleAssignmentRequest,
    RoleAssignmentResponse,
    SessionInfoResponse,
    Token,
    UserCreate,
    UserResponse,
    get_all_roles,
)
from nfm_db.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_service_account_token,
    decode_access_token,
    get_password_hash,
)

router = APIRouter(prefix="/auth", tags=["认证管理"])
settings = get_settings()

COOKIE_NAME = "access_token"


def _cookie_max_age(*, is_service_account: bool) -> int:
    """Return the auth cookie lifetime, in seconds, for the issued token.

    The cookie must expire with the JWT it carries.  When the cookie is the
    shorter of the two the browser silently stops sending credentials while
    the token is still valid, which surfaces as a sudden logout (NFM-2225 —
    a hardcoded 30-minute cookie outlived by an 8-hour token).

    Service accounts mint their tokens from ``service_jwt_ttl_minutes``
    rather than ``access_token_expire_minutes``, so mirror whichever knob
    actually produced the token.  Read at login time so a config change
    takes effect without touching this module.
    """
    minutes = (
        settings.service_jwt_ttl_minutes
        if is_service_account
        else settings.access_token_expire_minutes
    )
    return minutes * 60


def _validate_password_strength(password: str) -> None:
    """Enforce password policy: >=8 chars, must contain digits and letters."""
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        raise HTTPException(400, "Password must contain at least one letter")
    if not re.search(r"\d", password):
        raise HTTPException(400, "Password must contain at least one digit")


@router.post(
    "/login",
    response_model=Token,
    summary="用户登录",
    description="用户登录并获取访问令牌。\n\nLogin with username/password and receive an access token.",
)
@limiter.limit("20/minute")
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> Token:
    """用户登录，获取访问令牌。Sets HttpOnly cookie for browser clients."""
    # 支持用户名或邮箱登录
    result = await db.execute(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        )
    )
    user = result.scalar_one_or_none()

    if not user or not authenticate_user(user, form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # Update last login
    user.last_login = datetime.now(UTC)
    await db.commit()

    # Service accounts get a token carrying ``is_service_account`` + ``scope``
    # claims so downstream ``require_service_scope`` guards let them past the
    # ``/api/v1/extraction/ingest`` gate (and only that gate).  Human users
    # keep the plain ``{"sub": ...}`` payload — no behavioral change for them.
    if user.is_service_account:
        access_token = create_service_account_token(
            user,
            ServiceAccountScope.EXTRACTION_INGEST,
        )
    else:
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=access_token_expires,
        )

    # Set HttpOnly cookie for browser security (XSS-proof).
    # ``secure=True`` because the public endpoint is always served over
    # HTTPS via the Cloudflare Tunnel; ``secure=False`` would let the
    # token leak over plain HTTP and is rejected by modern browsers
    # when the page is HTTPS (CHIPS / PCT cookie policy). The browser
    # only sends the cookie back over HTTPS regardless, which is what
    # we want.
    response.set_cookie(
        COOKIE_NAME,
        access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=_cookie_max_age(is_service_account=user.is_service_account),
    )

    return Token(access_token=access_token)


@router.post("/logout")
async def logout(response: Response) -> ApiResponse[dict[str, str]]:
    """用户登出，清除认证 cookie。"""
    response.delete_cookie(COOKIE_NAME, path="/")
    return ApiResponse(success=True, data={"message": "Logged out"})


@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    summary="刷新访问令牌",
    description=(
        "Sliding-window session extension (NFM-2236).\n\n"
        "Re-issues the `access_token` cookie if the current one is still "
        "valid. The new token is returned in the JSON body so the "
        "frontend can schedule the next refresh.\n\n"
        "Returns 401 if the current cookie is missing, tampered, or "
        "expired — the frontend then surfaces an explicit re-auth "
        "prompt."
    ),
)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> RefreshTokenResponse:
    """Refresh the access token. Sliding-window; idempotent."""
    # Re-issue a fresh JWT whose ``exp`` matches the cookie ``Max-Age``
    # exactly.  Without this alignment the JWT would outlive the cookie
    # (login currently mints 8-hour JWTs but caps the cookie at 30 min,
    # which is a latent bug — refresh fixes it on the refresh path).
    # Service accounts keep their scope claim; humans get a plain
    # ``{"sub": ...}`` payload — same shape as login.
    access_token_expires = timedelta(seconds=COOKIE_MAX_AGE)
    if current_user.is_service_account:
        new_token = create_service_account_token(
            current_user,
            ServiceAccountScope.EXTRACTION_INGEST,
            expires_delta=access_token_expires,
        )
    else:
        new_token = create_access_token(
            data={"sub": str(current_user.id)},
            expires_delta=access_token_expires,
        )

    expires_at = datetime.now(UTC) + access_token_expires

    # Re-set the HttpOnly cookie with a fresh Max-Age. The cookie
    # attributes (HttpOnly, Secure, SameSite=Lax, Path) match ``/login``
    # exactly so the browser treats this as a refresh of the same
    # cookie, not a new one.
    response.set_cookie(
        COOKIE_NAME,
        new_token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )

    return RefreshTokenResponse(
        access_token=new_token,
        expires_at=expires_at,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册新用户",
    description="注册新用户（初始化阶段可用）。\n\nRegister a new user (available during initialization phase).",
)
@limiter.limit("3/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """注册新用户。Password must be >=8 chars with letters and digits."""
    _validate_password_strength(user_data.password)
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


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    summary="获取当前用户信息",
    description="获取当前认证用户的详细信息。\n\nGet the currently authenticated user's profile.",
)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
) -> ApiResponse[UserResponse]:
    """获取当前认证用户信息。"""
    return ApiResponse(
        success=True,
        data=UserResponse.model_validate(current_user),
    )


@router.get(
    "/session",
    response_model=ApiResponse[SessionInfoResponse],
    summary="获取当前会话信息（含令牌到期时间）",
    description=(
        "Returns the current user profile AND the JWT ``expires_at`` "
        "timestamp so the frontend ``SessionManager`` can schedule the "
        "first silent refresh (NFM-2236).\n\n"
        "Returns 401 if the user is not authenticated."
    ),
)
async def get_session_info(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SessionInfoResponse]:
    """Return user profile + JWT expiry for frontend session bootstrap."""
    # Re-decode the JWT to surface its ``exp`` claim.  ``get_current_user``
    # has already validated the token, so this decode cannot fail in
    # practice.  We deliberately do NOT trust the ``exp`` claim from a
    # cached user record — the JWT is the source of truth.
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Bearer-header path: the ``HTTPBearer`` dependency already
        # exposed the credentials via ``get_current_user``; we re-read
        # them by parsing the ``Authorization`` header here.
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    payload = decode_access_token(token or "")
    if not payload or "exp" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not derive token expiry from request",
        )
    expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)

    return ApiResponse(
        success=True,
        data=SessionInfoResponse(
            user=UserResponse.model_validate(current_user),
            expires_at=expires_at,
        ),
    )


@router.get(
    "/roles",
    response_model=ApiResponse[list[BlogRoleResponse]],
    summary="获取角色列表",
    description="获取所有博客角色列表（仅管理员）。\n\nList all blog roles (admin only).",
)
async def list_roles(
    current_user: User = Depends(require_admin_dep),
) -> ApiResponse[list[BlogRoleResponse]]:
    """获取所有博客角色列表（仅管理员）。"""
    roles = get_all_roles()
    return ApiResponse(
        success=True,
        data=roles,
    )


@router.put(
    "/users/{user_id}/role",
    response_model=ApiResponse[RoleAssignmentResponse],
    status_code=status.HTTP_200_OK,
    summary="分配用户角色",
    description="分配或移除用户博客角色（仅管理员）。\n\nAssign or remove a blog role for a user (admin only).",
)
async def assign_user_role(
    user_id: uuid.UUID,
    role_request: RoleAssignmentRequest,
    current_user: User = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RoleAssignmentResponse]:
    """分配或移除用户博客角色（仅管理员）。"""
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
