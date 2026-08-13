"""add_user_service_account_flag

Revision ID: 029_add_user_service_account_flag
Revises: 028_backfill_review_status_confidence
Create Date: 2026-07-28 15:30:00.000000

NFM-1973 / NFM-1972 AC-1: Add ``is_service_account`` flag to ``users`` so that
machine-to-machine callers (e.g. OntoFuel) can be provisioned with a username
that is scoped to ``/api/v1/extraction/ingest`` only and denied elsewhere.

Default ``FALSE`` so existing human users keep their current privileges.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '029_add_user_service_account_flag'
down_revision: str | Sequence[str] | None = '028_backfill_review_status_confidence'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``is_service_account`` boolean column to ``users``."""
    op.add_column(
        'users',
        sa.Column(
            'is_service_account',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment=(
                'True for machine-to-machine service accounts (e.g. OntoFuel). '
                'Service accounts authenticate via standard /auth/login but are '
                'restricted to specific ingest-style endpoints by RBAC scope.'
            ),
        ),
    )


def downgrade() -> None:
    """Drop ``is_service_account`` column from ``users``."""
    op.drop_column('users', 'is_service_account')