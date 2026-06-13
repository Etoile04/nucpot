"""Unit tests for User model permissions."""

import pytest
from uuid import uuid4

from nfm_db.models.user import BlogRole, Permission, User


class TestUserPermissions:
    """Test user permission system."""

    def test_admin_has_all_permissions(self) -> None:
        """Test that admin has all permissions."""
        user = User(
            id=uuid4(),
            username="admin",
            email="admin@example.com",
            hashed_password="hash",
            blog_role=BlogRole.ADMIN,
        )

        assert user.has_permission(Permission.CREATE_POST)
        assert user.has_permission(Permission.EDIT_POST)
        assert user.has_permission(Permission.DELETE_POST)
        assert user.has_permission(Permission.PUBLISH_POST)
        assert user.has_permission(Permission.REVIEW_POST)
        assert user.has_permission(Permission.ASSIGN_ROLES)

    def test_editor_has_limited_permissions(self) -> None:
        """Test that editor has create and edit permissions only."""
        user = User(
            id=uuid4(),
            username="editor",
            email="editor@example.com",
            hashed_password="hash",
            blog_role=BlogRole.EDITOR,
        )

        assert user.has_permission(Permission.CREATE_POST)
        assert user.has_permission(Permission.EDIT_POST)
        assert not user.has_permission(Permission.DELETE_POST)
        assert not user.has_permission(Permission.PUBLISH_POST)
        assert not user.has_permission(Permission.REVIEW_POST)
        assert not user.has_permission(Permission.ASSIGN_ROLES)

    def test_reviewer_has_review_permission_only(self) -> None:
        """Test that reviewer has review permission only."""
        user = User(
            id=uuid4(),
            username="reviewer",
            email="reviewer@example.com",
            hashed_password="hash",
            blog_role=BlogRole.REVIEWER,
        )

        assert not user.has_permission(Permission.CREATE_POST)
        assert not user.has_permission(Permission.EDIT_POST)
        assert not user.has_permission(Permission.DELETE_POST)
        assert not user.has_permission(Permission.PUBLISH_POST)
        assert user.has_permission(Permission.REVIEW_POST)
        assert not user.has_permission(Permission.ASSIGN_ROLES)

    def test_user_without_role_has_no_permissions(self) -> None:
        """Test that user without role has no permissions."""
        user = User(
            id=uuid4(),
            username="regular",
            email="regular@example.com",
            hashed_password="hash",
            blog_role=None,
        )

        assert not user.has_permission(Permission.CREATE_POST)
        assert not user.has_permission(Permission.EDIT_POST)
        assert not user.has_permission(Permission.DELETE_POST)
        assert not user.has_permission(Permission.PUBLISH_POST)
        assert not user.has_permission(Permission.REVIEW_POST)
        assert not user.has_permission(Permission.ASSIGN_ROLES)

    def test_permissions_property_returns_set(self) -> None:
        """Test that permissions property returns a set."""
        user = User(
            id=uuid4(),
            username="admin",
            email="admin@example.com",
            hashed_password="hash",
            blog_role=BlogRole.ADMIN,
        )

        permissions = user.permissions

        assert isinstance(permissions, set)
        assert len(permissions) > 0
