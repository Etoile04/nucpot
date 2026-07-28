"""User model with blog role support for RBAC."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    pass


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
    SUBMIT_FOR_REVIEW = "submit_for_review"
    PUBLISH_POST = "publish_post"
    REVIEW_POST = "review_post"
    ASSIGN_ROLES = "assign_roles"


class ServiceAccountScope(str, enum.Enum):
    """Authorized scopes for service accounts (NFM-1973 / NFM-1972 AC-1).

    Service accounts are machine-to-machine identities that authenticate via
    the standard ``/auth/login`` endpoint but must be authorized against a
    single, narrow scope.  Any HTTP handler that wishes to admit a service
    account must declare the required scope via the
    ``require_service_scope`` dependency; otherwise the request is denied
    with ``403 Forbidden``.

    Adding a new scope is a two-step process:
    1. Add the enum member below.
    2. Decorate the target endpoint with
       ``Depends(require_service_scope(ServiceAccountScope.<NEW>))``.
    """

    EXTRACTION_INGEST = "extraction:ingest"


class User(TimestampMixin, Base):
    """User model with blog role support for role-based access control.

    The same table holds both human users (with a ``blog_role``) and
    service accounts (with ``is_service_account=True`` and zero
    ``blog_role``).  The two populations are disjoint — service accounts
    authenticate via ``/auth/login`` like everyone else but are gated
    by ``ServiceAccountScope`` instead of ``BlogRole``.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "blog_role IN ('admin', 'editor', 'reviewer')",
            name="check_blog_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    _blog_role: Mapped[str | None] = mapped_column(
        "blog_role",
        String(20),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_service_account: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Profile fields (migrated from Supabase profiles table)
    affiliation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def blog_role(self) -> BlogRole | None:
        """Get the blog role as an enum."""
        if self._blog_role is None:
            return None
        return BlogRole(self._blog_role)

    @blog_role.setter
    def blog_role(self, value: BlogRole | None) -> None:
        """Set the blog role from an enum."""
        self._blog_role = value.value if value else None

    @property
    def permissions(self) -> set[Permission]:
        """Get user permissions based on blog role.

        Service accounts have **no** blog permissions by design; their
        access is governed exclusively by ``ServiceAccountScope`` checks
        on the endpoints they are permitted to call.
        """
        if self.is_service_account or not self.blog_role:
            return set()

        role_permissions = {
            BlogRole.ADMIN: {
                Permission.CREATE_POST,
                Permission.EDIT_POST,
                Permission.DELETE_POST,
                Permission.SUBMIT_FOR_REVIEW,
                Permission.PUBLISH_POST,
                Permission.REVIEW_POST,
                Permission.ASSIGN_ROLES,
            },
            BlogRole.EDITOR: {
                Permission.CREATE_POST,
                Permission.EDIT_POST,
                Permission.SUBMIT_FOR_REVIEW,
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
        kind = "service" if self.is_service_account else "user"
        role = self.blog_role.value if self.blog_role else None
        return (
            f"<{kind} id={self.id!s} "
            f"username={self.username!r} "
            f"role={role}>"
        )
