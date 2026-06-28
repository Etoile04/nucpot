"""create potentials table

Revision ID: 003
Revises: 9c15710c6321
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | Sequence[str] | None = "9c15710c6321"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "potentials",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("subtype", sa.String(64), nullable=True),
        sa.Column("format", sa.String(64), nullable=True),
        sa.Column("elements", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("system_name", sa.String(128), nullable=True),
        sa.Column("system_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("applicability", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("references", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("developers", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("verified_props", sa.JSON(), nullable=True),
        sa.Column("sim_software", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("lammps_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("file_url", sa.String(512), nullable=True),
        sa.Column("file_hash", sa.String(128), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("source_doi", sa.String(128), nullable=True),
        sa.Column("license", sa.String(64), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("version", sa.String(16), nullable=False, server_default=sa.text("'1.0'")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'published'")),
        sa.Column("extra", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_potentials_type", "potentials", ["type"])
    op.create_index("ix_potentials_status", "potentials", ["status"])


def downgrade() -> None:
    op.drop_index("ix_potentials_status", table_name="potentials")
    op.drop_index("ix_potentials_type", table_name="potentials")
    op.drop_table("potentials")
