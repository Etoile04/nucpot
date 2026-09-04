"""Add domain_expert to blog_role_enum + CHECK constraint (BUG-08, NFM-4089).

The ORM declared ``BlogRole.DOMAIN_EXPERT`` and the table CHECK in
models/user.py already lists it, but production still had the 3-value
enum/CHECK, so creating a domain_expert user (or minting an invite)
would violate the DB constraint. This migration extends both, aligned
with NFM-858's requirements for domain-expert review workflows.

Revision ID: 082_blog_role_domain_expert
Revises: 081_create_feature_flags_table
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "082_blog_role_domain_expert"
down_revision: str | Sequence[str] | None = "081_create_feature_flags_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. extend the PG enum (guarded: ADDVALUE is transactional-safe on PG10+
    #    but not idempotent, so check first)
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'blog_role_enum' AND e.enumlabel = 'domain_expert'
          ) THEN
            ALTER TYPE blog_role_enum ADD VALUE 'domain_expert';
          END IF;
        END
        $$;
        """
    )

    # 2. swap the CHECK constraint to the 4-value version
    op.drop_constraint("check_blog_role", "users", type_="check")
    op.create_check_constraint(
        "check_blog_role",
        "users",
        "blog_role IN ('admin', 'editor', 'reviewer', 'domain_expert')",
    )


def downgrade() -> None:
    # PG cannot remove an enum value without type rebuild; the constraint
    # rollback is safe because no rows may legally hold domain_expert after
    # the constraint tightens only if none exist — guard defensively.
    op.execute("DELETE FROM users WHERE blog_role = 'domain_expert'")
    op.drop_constraint("check_blog_role", "users", type_="check")
    op.create_check_constraint(
        "check_blog_role",
        "users",
        "blog_role IN ('admin', 'editor', 'reviewer')",
    )
    # enum value left in place (harmless, unreferenced)
