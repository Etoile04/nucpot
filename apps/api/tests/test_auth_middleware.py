"""Unit tests for authorization middleware."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from nfm_db.api.v1.auth import (
    get_current_active_user,
    get_current_user,
    require_blog_role,
    require_permission,
)
from nfm_db.models.user import BlogRole, Permission, User


class TestGetCurrentUser:
    """Test get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self, db_session) -> None:
        """Test that valid token returns the user."""
        from nfm_db.services.auth_service import create_access_token

        user = User(
            id=uuid4(),
            username="testuser",
            email="test@example.com",
            hashed_password="hash",
        )
        token = create_access_token({"sub": str(user.id)})

        credentials = MagicMock(type="bearer", credentials=token)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result

        result = await get_current_user(credentials, mock_db)

        assert result == user

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self, db_session) -> None:
        """Test that invalid token raises HTTP 401."""
        credentials = MagicMock(type="bearer", credentials="invalid_token")
        mock_db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials, mock_db)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_missing_user_raises_401(self, db_session) -> None:
        """Test that missing user raises HTTP 401."""
        from nfm_db.services.auth_service import create_access_token

        user_id = uuid4()
        token = create_access_token({"sub": str(user_id)})

        credentials = MagicMock(type="bearer", credentials=token)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials, mock_db)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestGetCurrentActiveUser:
    """Test get_current_active_user dependency."""

    @pytest.mark.asyncio
    async def test_active_user_returns_user(self) -> None:
        """Test that active user is returned."""
        user = User(
            id=uuid4(),
            username="testuser",
            email="test@example.com",
            hashed_password="hash",
            is_active=True,
        )

        result = await get_current_active_user(user)

        assert result == user

    @pytest.mark.asyncio
    async def test_inactive_user_raises_403(self) -> None:
        """Test that inactive user raises HTTP 403."""
        user = User(
            id=uuid4(),
            username="testuser",
            email="test@example.com",
            hashed_password="hash",
            is_active=False,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_current_active_user(user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


class TestRequireBlogRole:
    """Test require_blog_role dependency factory."""

    @pytest.mark.asyncio
    async def test_user_with_allowed_role_succeeds(self) -> None:
        """Test user with allowed role passes."""
        user = User(
            id=uuid4(),
            username="admin",
            email="admin@example.com",
            hashed_password="hash",
            blog_role=BlogRole.ADMIN,
        )

        dependency = require_blog_role(BlogRole.ADMIN)
        result = await dependency(user)

        assert result == user

    @pytest.mark.asyncio
    async def test_user_with_different_role_raises_403(self) -> None:
        """Test user with different role raises HTTP 403."""
        user = User(
            id=uuid4(),
            username="editor",
            email="editor@example.com",
            hashed_password="hash",
            blog_role=BlogRole.EDITOR,
        )

        dependency = require_blog_role(BlogRole.ADMIN)

        with pytest.raises(HTTPException) as exc_info:
            await dependency(user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_user_without_role_raises_403(self) -> None:
        """Test user without role raises HTTP 403."""
        user = User(
            id=uuid4(),
            username="regular",
            email="regular@example.com",
            hashed_password="hash",
            blog_role=None,
        )

        dependency = require_blog_role(BlogRole.ADMIN)

        with pytest.raises(HTTPException) as exc_info:
            await dependency(user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


class TestRequirePermission:
    """Test require_permission dependency factory."""

    @pytest.mark.asyncio
    async def test_user_with_permission_succeeds(self) -> None:
        """Test user with required permission passes."""
        user = User(
            id=uuid4(),
            username="admin",
            email="admin@example.com",
            hashed_password="hash",
            blog_role=BlogRole.ADMIN,
        )

        dependency = require_permission(Permission.DELETE_POST)
        result = await dependency(user)

        assert result == user

    @pytest.mark.asyncio
    async def test_user_without_permission_raises_403(self) -> None:
        """Test user without required permission raises HTTP 403."""
        user = User(
            id=uuid4(),
            username="editor",
            email="editor@example.com",
            hashed_password="hash",
            blog_role=BlogRole.EDITOR,
        )

        dependency = require_permission(Permission.DELETE_POST)

        with pytest.raises(HTTPException) as exc_info:
            await dependency(user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
