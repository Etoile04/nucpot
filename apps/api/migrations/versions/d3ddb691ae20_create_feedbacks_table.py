"""create feedbacks table

Revision ID: d3ddb691ae20
Revises:
Create Date: 2026-06-08 20:53:49.754298

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3ddb691ae20"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create feedbacks table with enums."""
    feedback_type_enum = sa.Enum(
        "bug_report",
        "feature_request",
        "data_correction",
        "usage_inquiry",
        name="feedback_type_enum",
    )
    priority_enum = sa.Enum(
        "urgent",
        "high",
        "medium",
        "low",
        name="priority_enum",
    )
    feedback_status_enum = sa.Enum(
        "open",
        "classified",
        "assigned",
        "in_progress",
        "resolved",
        "closed",
        name="feedback_status_enum",
    )

    feedback_type_enum.create(op.get_bind(), checkfirst=True)
    priority_enum.create(op.get_bind(), checkfirst=True)
    feedback_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "feedback_type",
            feedback_type_enum,
            nullable=False,
        ),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("page_url", sa.String(500), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("priority", priority_enum, nullable=False, server_default="medium"),
        sa.Column(
            "status",
            feedback_status_enum,
            nullable=False,
            server_default="open",
        ),
        sa.Column("assignee", sa.String(100), nullable=True),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop feedbacks table and enums."""
    op.drop_table("feedbacks")

    op.execute("DROP TYPE IF EXISTS feedback_status_enum")
    op.execute("DROP TYPE IF EXISTS priority_enum")
    op.execute("DROP TYPE IF EXISTS feedback_type_enum")
