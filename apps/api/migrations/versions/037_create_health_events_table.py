"""create health_events table for structured silent-failure tracking

Revision ID: 037_create_health_events_table
Revises: 036_merge_chain_A_and_B
Create Date: 2026-07-31

NFM-2220: Add a ``health_events`` table that services write to when
they catch exceptions that were previously silently swallowed (``except:
pass``).  The table is append-only and indexed for alert queries.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037_create_health_events_table"
down_revision: str | Sequence[str] | None = "036_merge_chain_A_and_B"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("source_service", sa.String(100), nullable=False),
        sa.Column(
            "context",
            sa.dialects.postgresql.JSONB,
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_health_events_created_at", "health_events", ["created_at"])
    op.create_index("ix_health_events_event_type", "health_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_health_events_event_type")
    op.drop_index("ix_health_events_created_at")
    op.drop_table("health_events")
