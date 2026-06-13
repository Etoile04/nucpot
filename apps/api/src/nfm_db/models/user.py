"""User model with blog role support for RBAC."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class BlogRole(str, enum.Enum):
    """Blog administration roles for RBAC."""

    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"


class Permission(str, enum.Enum):
    """Blog permissions mapping."""

    CREATE_POST = "create_post"
    EDIT_POST = "edit_post"
    DELETE_POST = "delete_post"
    PUBLISH_POST = "publish_post"
    REVIEW_POST = "review_post"
    ASSIGN_ROLES = "assign_roles"


class User(TimestampMixin, Base):
    """User model with blog role support for role-based access control."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "blog_role IN ('admin', 'editor', 'reviewer')",
            name="check_blog_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    blog_role: Mapped[BlogRole | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def permissions(self) -> set[Permission]:
        """Get user permissions based on blog role."""
        if not self.blog_role:
            return set()

        role_permissions = {
            BlogRole.ADMIN: {
                Permission.CREATE_POST,
                Permission.EDIT_POST,
                Permission.DELETE_POST,
                Permission.PUBLISH_POST,
                Permission.REVIEW_POST,
                Permission.ASSIGN_ROLES,
            },
            BlogRole.EDITOR: {
                Permission.CREATE_POST,
                Permission.EDIT_POST,
            },
            BlogRole.REVIEWER: {
                Permission.REVIEW_POST,
            },
        }
        return role_permissions.get(self.blog_role, set())

    def has_permission(self, permission: Permission) -> bool:
        """Check if user has a specific permission."""
        return permission in self.permissions

    def __repr__(self) -> str:
        return (
            f"<User id={self.id!s} "
            f"username={self.username!r} "
            f"role={self.blog_role.value if self.blog_role else None}>"
        )
