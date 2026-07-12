"""add title column to blog_posts

Revision ID: 008
Revises: 007
Create Date: 2026-07-01 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: str | Sequence[str] | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add title column to blog_posts table."""
    op.add_column(
        "blog_posts",
        sa.Column("title", sa.String(255), nullable=False, server_default="Untitled"),
    )
    op.alter_column("blog_posts", "title", server_default=None)


def downgrade() -> None:
    """Remove title column from blog_posts table."""
    op.drop_column("blog_posts", "title")
