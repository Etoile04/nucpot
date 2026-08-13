"""Unit tests for authentication and RBAC functionality."""

import pytest
from sqlalchemy.exc import IntegrityError

from nfm_db.models import BlogRole, Permission, User


@pytest.mark.unit
@pytest.mark.asyncio
class TestUserModel:
    """Test User model and RBAC functionality."""

    async def test_user_creation(self, db_session):
        """Test creating a new user."""
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.id is not None
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.blog_role is None

    async def test_user_with_blog_role(self, db_session):
        """Test creating a user with blog role."""
        user = User(
            username="editor",
            email="editor@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.EDITOR,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.blog_role == BlogRole.EDITOR

    async def test_admin_permissions(self, db_session):
        """Test admin user has all permissions."""
        user = User(
            username="admin",
            email="admin@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.ADMIN,
        )

        expected_permissions = {
            Permission.CREATE_POST,
            Permission.EDIT_POST,
            Permission.DELETE_POST,
            Permission.PUBLISH_POST,
            Permission.REVIEW_POST,
            Permission.SUBMIT_FOR_REVIEW,
            Permission.ASSIGN_ROLES,
        }

        assert user.permissions == expected_permissions
        assert user.has_permission(Permission.ASSIGN_ROLES)
        assert user.has_permission(Permission.DELETE_POST)

    async def test_editor_permissions(self, db_session):
        """Test editor user has limited permissions."""
        user = User(
            username="editor",
            email="editor@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.EDITOR,
        )

        expected_permissions = {
            Permission.CREATE_POST,
            Permission.EDIT_POST,
            Permission.SUBMIT_FOR_REVIEW,
        }

        assert user.permissions == expected_permissions
        assert user.has_permission(Permission.CREATE_POST)
        assert user.has_permission(Permission.EDIT_POST)
        assert user.has_permission(Permission.SUBMIT_FOR_REVIEW)
        assert not user.has_permission(Permission.DELETE_POST)
        assert not user.has_permission(Permission.REVIEW_POST)

    async def test_reviewer_permissions(self, db_session):
        """Test reviewer user has review permissions only."""
        user = User(
            username="reviewer",
            email="reviewer@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.REVIEWER,
        )

        expected_permissions = {
            Permission.REVIEW_POST,
        }

        assert user.permissions == expected_permissions
        assert user.has_permission(Permission.REVIEW_POST)
        assert not user.has_permission(Permission.CREATE_POST)
        assert not user.has_permission(Permission.EDIT_POST)

    async def test_user_without_role_has_no_permissions(self, db_session):
        """Test user without blog role has no permissions."""
        user = User(
            username="normal",
            email="normal@example.com",
            hashed_password="hashed_password_here",
        )

        assert user.permissions == set()
        assert not user.has_permission(Permission.CREATE_POST)
        assert not user.has_permission(Permission.REVIEW_POST)

    async def test_unique_username(self, db_session):
        """Test username uniqueness constraint."""
        user1 = User(
            username="duplicate",
            email="user1@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user1)
        await db_session.commit()

        user2 = User(
            username="duplicate",
            email="user2@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user2)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_unique_email(self, db_session):
        """Test email uniqueness constraint."""
        user1 = User(
            username="user1",
            email="duplicate@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user1)
        await db_session.commit()

        user2 = User(
            username="user2",
            email="duplicate@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user2)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_user_repr(self, db_session):
        """Test user __repr__ method."""
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.ADMIN,
        )

        repr_str = repr(user)
        assert "testuser" in repr_str
        assert "admin" in repr_str


@pytest.mark.unit
class TestBlogRole:
    """Test BlogRole enum."""

    def test_blog_role_values(self):
        """Test blog role enum values."""
        assert BlogRole.ADMIN.value == "admin"
        assert BlogRole.EDITOR.value == "editor"
        assert BlogRole.REVIEWER.value == "reviewer"


@pytest.mark.unit
class TestPermission:
    """Test Permission enum."""

    def test_permission_values(self):
        """Test permission enum values."""
        assert Permission.CREATE_POST.value == "create_post"
        assert Permission.EDIT_POST.value == "edit_post"
        assert Permission.DELETE_POST.value == "delete_post"
        assert Permission.PUBLISH_POST.value == "publish_post"
        assert Permission.REVIEW_POST.value == "review_post"
        assert Permission.SUBMIT_FOR_REVIEW.value == "submit_for_review"
        assert Permission.ASSIGN_ROLES.value == "assign_roles"
