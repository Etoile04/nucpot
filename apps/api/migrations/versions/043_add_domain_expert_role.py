"""Add domain_expert value to blog_role_enum (NFM-2573-T1).

NFM-2578 — foundation for the ontology management module. The new
``domain_expert`` role grants access to ontology editing endpoints
without full admin/editor privileges.

PostgreSQL:
  ``ALTER TYPE blog_role_enum ADD VALUE IF NOT EXISTS 'domain_expert'``
  (PG 9.6+ supports adding enum values; no table rewrite needed).

SQLite:
  Batch-recreate the ``check_blog_role`` CHECK constraint to include
  ``'domain_expert'``. SQLite has no native enum types — the original
  constraint was created in migration 001.

Downgrade:
  PostgreSQL — ``ALTER TYPE blog_role_enum DROP VALUE 'domain_expert'``
  (PG 12+).  On older PG, this is a no-op (enum values cannot be
  removed without recreating the type).
  SQLite — recreate CHECK constraint without ``'domain_expert'``.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "043_add_domain_expert_role"
down_revision: str | Sequence[str] | None = "042_extraction_step_and_chunk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_VALUE = "domain_expert"
_OLD_VALUES = "'admin', 'editor', 'reviewer'"
_ALL_VALUES = f"'admin', 'editor', 'reviewer', '{_NEW_VALUE}'"
_CONSTRAINT = "check_blog_role"


def upgrade() -> None:
    """Add domain_expert to the blog_role enum / check constraint."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"ALTER TYPE blog_role_enum ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'"
        )
        return

    # SQLite: drop and recreate the CHECK constraint with the new value.
    op.drop_constraint(_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "users",
        f"blog_role IN ({_ALL_VALUES})",
    )


def downgrade() -> None:
    """Remove domain_expert from the blog_role enum / check constraint."""
    if op.get_bind().dialect.name == "postgresql":
        # PG 12+ supports DROP VALUE; older versions are a no-op.
        try:
            op.execute(
                f"ALTER TYPE blog_role_enum DROP VALUE '{_NEW_VALUE}'"
            )
        except Exception:
            # Pre-PG-12: enum values cannot be removed. Skip silently.
            pass
        return

    # SQLite: recreate CHECK constraint without domain_expert.
    op.drop_constraint(_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "users",
        f"blog_role IN ({_OLD_VALUES})",
    )
