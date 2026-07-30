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
from sqlalchemy.dialects import postgresql

revision: str = "037_create_health_events_table"
down_revision: str | Sequence[str] | None = "036_merge_chain_A_and_B"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Enum sets are mirrored from :mod:`nfm_db.services.health_event_emitter`
# so the CHECK constraints match what the application actually emits.
_ALLOWED_EVENT_TYPES = (
    "fallback_triggered",
    "validation_drop",
    "category_coercion_fail",
    "asyncio_crash",
    "generic_silent_catch",
)
_ALLOWED_SEVERITIES = ("info", "warning", "error", "critical")


def upgrade() -> None:
    op.create_table(
        "health_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("source_service", sa.String(100), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB,
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            + ", ".join(f"'{v}'" for v in _ALLOWED_EVENT_TYPES)
            + ")",
            name="ck_health_events_event_type",
        ),
        sa.CheckConstraint(
            "severity IN (" + ", ".join(f"'{v}'" for v in _ALLOWED_SEVERITIES) + ")",
            name="ck_health_events_severity",
        ),
    )
    op.create_index("ix_health_events_created_at", "health_events", ["created_at"])
    op.create_index("ix_health_events_event_type", "health_events", ["event_type"])
    op.create_index("ix_health_events_severity", "health_events", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_health_events_severity", table_name="health_events")
    op.drop_index("ix_health_events_event_type", table_name="health_events")
    op.drop_index("ix_health_events_created_at", table_name="health_events")
    op.drop_table("health_events")
