"""add verification_status column to potentials

Revision ID: 005
Revises: 004
Create Date: 2026-06-19

Adds a first-class ``verification_status`` column to ``potentials`` so that
nucpot-autovc can write the verification lifecycle (unverified -> pending ->
verified|failed) without read-modify-write races on the ``extra`` JSON blob.

``server_default='unverified'`` back-fills existing seed rows and gives every
future WS1 insert a safe starting state forward-looking.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | Sequence[str] | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "potentials",
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
    )
    op.create_check_constraint(
        "ck_potentials_verification_status",
        "potentials",
        "verification_status IN ('unverified', 'pending', 'verified', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_potentials_verification_status", "potentials", type_="check")
    op.drop_column("potentials", "verification_status")
