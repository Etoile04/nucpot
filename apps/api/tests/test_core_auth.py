"""Unit tests for nfm_db.core.auth module (NFM-581).

Tests the core auth middleware: custom error classes, get_current_user
placeholder, get_current_active_user, RequirePermission, and RequireRole
dependency factories.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from nfm_db.core.auth import (
    AuthenticationError,
    ForbiddenError,
    RequirePermission,
    RequireRole,
    UnauthorizedError,
    get_current_active_user,
    get_current_user,
    require_admin,
    require_admin_or_reviewer,
    require_create_post,
    require_delete_post,
    require_edit_post,
    require_editor,
    require_publish_post,
    require_review_post,
    require_reviewer,
    require_assign_roles,
)
from nfm_db.models.user import BlogRole, Permission, User


# ---------------------------------------------------------------------------
# Custom error classes
# ---------------------------------------------------------------------------


class TestUnauthorizedError:
    """Test UnauthorizedError custom exception."""

    def test_default_message(self) -> None:
        exc = UnauthorizedError()
        assert exc.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.detail == "Could not validate credentials"
        assert "WWW-Authenticate" in exc.headers

    def test_custom_message(self) -> None:
        exc = UnauthorizedError("Token expired")
        assert exc.detail == "Token expired"
        assert exc.status_code == 401

    def test_is_http_exception(self) -> None:
        exc = UnauthorizedError()
        assert isinstance(exc, HTTPException)


class TestForbiddenError:
    """Test ForbiddenError custom exception."""

    def test_default_message(self) -> None:
        exc = ForbiddenError()
        assert exc.status_code == status.HTTP_403_FORBIDDEN
        assert exc.detail == "Insufficient permissions"

    def test_custom_message(self) -> None:
        exc = ForbiddenError("Access denied to admin area")
        assert exc.detail == "Access denied to admin area"
        assert exc.status_code == 403


class TestAuthenticationError:
    """Test AuthenticationError custom exception."""

    def test_default_message(self) -> None:
        exc = AuthenticationError()
        assert exc.status_code == status.HTTP_403_FORBIDDEN
        assert exc.detail == "User account is inactive"

    def test_custom_message(self) -> None:
        exc = AuthenticationError("Account suspended")
        assert exc.detail == "Account suspended"


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    """Test core.auth get_current_user placeholder dependency."""

    @pytest.mark.asyncio
    async def test_no_credentials_raises_unauthorized(self) -> None:
        """Missing credentials raise 401 UnauthorizedError."""
        with pytest.raises(UnauthorizedError) as exc_info:
            await get_current_user(None)
        assert "No authentication credentials provided" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_placeholder_raises_unauthorized(self) -> None:
        """Even with credentials, placeholder raises 401 (JWT not implemented)."""
        credentials = MagicMock(type="bearer", credentials="some-token")
        with pytest.raises(UnauthorizedError) as exc_info:
            await get_current_user(credentials)
        assert "JWT authentication not yet implemented" in exc_info.value.detail


# ---------------------------------------------------------------------------
# get_current_active_user
# ---------------------------------------------------------------------------


class TestGetCurrentActiveUser:
    """Test core.auth get_current_active_user dependency."""

    @pytest.mark.asyncio
    async def test_active_user_returns_user(self) -> None:
        """Active user is returned unchanged."""
        user = User(
            id=uuid4(),
            username="activeuser",
            email="active@test.com",
            hashed_password="hash",
            is_active=True,
        )
        result = await get_current_active_user(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_inactive_user_raises_authentication_error(self) -> None:
        """Inactive user raises AuthenticationError with 403."""
        user = User(
            id=uuid4(),
            username="inactiveuser",
            email="inactive@test.com",
            hashed_password="hash",
            is_active=False,
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await get_current_active_user(user)
        assert "inactive" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# RequirePermission
# ---------------------------------------------------------------------------


class TestRequirePermission:
    """Test RequirePermission dependency factory."""

    @pytest.mark.asyncio
    async def test_user_with_permission_succeeds(self) -> None:
        """Admin user with DELETE_POST permission passes."""
        user = User(
            id=uuid4(), username="admin", email="a@t.com",
            hashed_password="h", blog_role=BlogRole.ADMIN,
        )
        dep = RequirePermission(Permission.DELETE_POST)
        result = await dep(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_user_without_permission_raises_forbidden(self) -> None:
        """Editor lacking PUBLISH_POST permission raises 403."""
        user = User(
            id=uuid4(), username="editor", email="e@t.com",
            hashed_password="h", blog_role=BlogRole.EDITOR,
        )
        dep = RequirePermission(Permission.PUBLISH_POST)
        with pytest.raises(ForbiddenError) as exc_info:
            await dep(user)
        assert "publish_post" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_user_no_role_raises_forbidden(self) -> None:
        """User without blog role has no permissions."""
        user = User(
            id=uuid4(), username="norole", email="n@t.com",
            hashed_password="h",
        )
        dep = RequirePermission(Permission.CREATE_POST)
        with pytest.raises(ForbiddenError):
            await dep(user)


# ---------------------------------------------------------------------------
# RequireRole
# ---------------------------------------------------------------------------


class TestRequireRole:
    """Test RequireRole dependency factory."""

    @pytest.mark.asyncio
    async def test_user_with_matching_role_succeeds(self) -> None:
        """Admin matches require_admin."""
        user = User(
            id=uuid4(), username="admin", email="a@t.com",
            hashed_password="h", blog_role=BlogRole.ADMIN,
        )
        dep = RequireRole(BlogRole.ADMIN)
        result = await dep(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_user_with_no_role_raises_forbidden(self) -> None:
        """User without blog role raises 403."""
        user = User(
            id=uuid4(), username="norole", email="n@t.com",
            hashed_password="h",
        )
        dep = RequireRole(BlogRole.ADMIN)
        with pytest.raises(ForbiddenError) as exc_info:
            await dep(user)
        assert "no blog role" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_user_with_wrong_role_raises_forbidden(self) -> None:
        """Reviewer doesn't match require_admin."""
        user = User(
            id=uuid4(), username="reviewer", email="r@t.com",
            hashed_password="h", blog_role=BlogRole.REVIEWER,
        )
        dep = RequireRole(BlogRole.ADMIN)
        with pytest.raises(ForbiddenError) as exc_info:
            await dep(user)
        assert "reviewer" in exc_info.value.detail.lower()
        assert "admin" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_multi_role_accepts_any_match(self) -> None:
        """RequireRole(ADMIN, REVIEWER) accepts reviewer."""
        user = User(
            id=uuid4(), username="rev", email="r@t.com",
            hashed_password="h", blog_role=BlogRole.REVIEWER,
        )
        dep = RequireRole(BlogRole.ADMIN, BlogRole.REVIEWER)
        result = await dep(user)
        assert result is user


# ---------------------------------------------------------------------------
# Convenience dependency aliases
# ---------------------------------------------------------------------------


class TestConvenienceAliases:
    """Test that convenience dependency aliases exist and are callable."""

    def test_role_aliases_are_callable(self) -> None:
        """All role convenience deps should be callable factories."""
        assert callable(require_admin)
        assert callable(require_editor)
        assert callable(require_reviewer)
        assert callable(require_admin_or_reviewer)

    def test_permission_aliases_are_callable(self) -> None:
        """All permission convenience deps should be callable factories."""
        assert callable(require_create_post)
        assert callable(require_edit_post)
        assert callable(require_delete_post)
        assert callable(require_publish_post)
        assert callable(require_review_post)
        assert callable(require_assign_roles)
