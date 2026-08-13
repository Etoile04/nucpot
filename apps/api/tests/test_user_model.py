"""Unit tests for User model permissions."""

from uuid import uuid4

import pytest
from sqlalchemy import Enum as SAEnum

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


# ---------------------------------------------------------------------------
# NFM-1997: ``users.blog_role`` column type must match the live DB schema.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blog_role_column_is_native_pg_enum() -> None:
    """``users.blog_role`` is declared as a native PostgreSQL enum.

    Regression guard for NFM-1997.  Migration ``001_create_users_table``
    creates the column as ``blog_role blog_role_enum`` (a real PG enum
    type), but the model previously declared it ``String(20)``.  That
    mismatch made asyncpg send the INSERT parameter typed as
    ``character varying`` while the column is the enum type, so Postgres
    raised ``DatatypeMismatchError`` and the ``create-service-account``
    CLI crashed (E2E QA NFM-1985, run 09ee4597).

    The model MUST therefore declare an ``Enum`` type whose ``name`` matches
    the DB type (``blog_role_enum``), native on PostgreSQL, whose stored
    values are the lowercase labels the type was created with.  Migration 001
    owns the ``CREATE TYPE``, so the enum's ``name`` must line up with it
    exactly (SQLAlchemy's ``create_all`` checks first and will not duplicate
    an existing type).
    """
    column = User.__table__.columns["blog_role"]
    col_type = column.type

    assert isinstance(col_type, SAEnum), (
        f"blog_role must be a native Enum, got {type(col_type).__name__}"
    )
    assert col_type.native_enum is True, "blog_role enum must be native on PG"
    assert col_type.name == "blog_role_enum"
    # values_callable yields the lowercase labels the DB enum was created with.
    assert set(col_type.enums) == {"admin", "editor", "reviewer", "domain_expert"}
