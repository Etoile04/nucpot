"""add user profile fields (affiliation, title, phone)

Revision ID: 015
Revises: 014
Create Date: 2026-07-15 10:00:00.000000

Adds three nullable string columns to the ``users`` table to
replace the fields that previously lived in the Supabase
``profiles`` table:

    affiliation VARCHAR(255) NULL
    title       VARCHAR(255) NULL
    phone       VARCHAR(64)  NULL

All columns are added with ``server_default=None`` so existing
rows simply get NULL values and the migration is non-blocking.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015b"
down_revision: str | Sequence[str] | None = "015a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add affiliation, title, phone columns to users."""
    op.add_column(
        "users",
        sa.Column(
            "affiliation",
            sa.String(255),
            nullable=True,
            server_default=None,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "title",
            sa.String(255),
            nullable=True,
            server_default=None,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "phone",
            sa.String(64),
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    """Remove affiliation, title, phone columns from users."""
    op.drop_column("users", "phone")
    op.drop_column("users", "title")
    op.drop_column("users", "affiliation")
