"""create feedbacks table

Revision ID: d3ddb691ae20
Revises:
Create Date: 2026-06-08 20:53:49.754298

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3ddb691ae20"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create feedbacks table with enums - all via raw SQL (idempotent)."""
    op.execute("""
        CREATE TYPE IF NOT EXISTS feedback_type_enum AS ENUM (
            'bug_report', 'feature_request', 'data_correction', 'usage_inquiry'
        )
    """)
    op.execute("""
        CREATE TYPE IF NOT EXISTS priority_enum AS ENUM (
            'urgent', 'high', 'medium', 'low'
        )
    """)
    op.execute("""
        CREATE TYPE IF NOT EXISTS feedback_status_enum AS ENUM (
            'open', 'classified', 'assigned', 'in_progress',
            'resolved', 'closed'
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            feedback_type feedback_type_enum NOT NULL,
            title VARCHAR(100) NOT NULL,
            description TEXT NOT NULL,
            page_url VARCHAR(500),
            contact_email VARCHAR(255),
            priority priority_enum NOT NULL DEFAULT 'medium',
            status feedback_status_enum NOT NULL DEFAULT 'open',
            assignee VARCHAR(100),
            resolution TEXT,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    """Drop feedbacks table and enums."""
    op.execute("DROP TABLE IF EXISTS feedbacks")
    op.execute("DROP TYPE IF EXISTS feedback_status_enum")
    op.execute("DROP TYPE IF EXISTS priority_enum")
    op.execute("DROP TYPE IF EXISTS feedback_type_enum")
