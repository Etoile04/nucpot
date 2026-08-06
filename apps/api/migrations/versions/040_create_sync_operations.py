"""Persist Hub sync operations as an isolated head.

The main alembic chain (d3ddb691ae20 -> 022 -> ...) cannot be applied to a
clean PostgreSQL database because the mid-chain migrations 011-021 reference
objects (e.g. ``extraction_results``) that are only created by their
siblings, and several files share the same numeric revision id
(``revision: "013"``). ``alembic upgrade head`` stops at 022 with
``UndefinedTableError`` on a fresh DB and the chain is unreachable from
``head`` in both directions.

For the C-full E2E (NFM-2029) we only need ``sync_operations`` plus the
``units`` / ``unit_conversions`` / ``property_categories`` seed that
``009`` + ``010`` provide, plus a ``hub_nodes`` row for the resource daemons
to register against. The Hub also has a hard FK to ``resource_nodes`` (which
``d3ddb691ae20`` line creates), so we make ``040`` an independent head
``down_revision = "010"``. Operationally we stamp the alembic version to
``010`` after the seed step runs (see ``apps/api/e2e/seed_hub.py`` for the
production path) and re-run ``alembic upgrade`` to apply this migration
without traversing the broken mid-chain.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "040_create_sync_operations"
down_revision: str | Sequence[str] | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # ``hub_nodes`` and ``resource_nodes`` both live on the broken
    # d3ddb691ae20 -> 022 chain. Recreate just the columns the new
    # ``sync_operations`` FK + Hub registration path need. Idempotent
    # so re-running on a DB that already has the full table is a no-op.
    bind.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS hub_nodes ("
            " id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            " name VARCHAR(200) NOT NULL,"
            " api_endpoint VARCHAR(500) NOT NULL,"
            " public_key VARCHAR(2000),"
            " status VARCHAR(50) NOT NULL DEFAULT 'active',"
            " last_heartbeat VARCHAR(50),"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS resource_nodes ("
            " id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            " hub_node_id UUID NOT NULL,"
            " name VARCHAR(200) NOT NULL,"
            " node_type VARCHAR(50) NOT NULL,"
            " api_endpoint VARCHAR(500) NOT NULL,"
            " public_key VARCHAR(2000),"
            " status VARCHAR(50) NOT NULL DEFAULT 'active',"
            " last_heartbeat VARCHAR(50),"
            " offline_since VARCHAR(50),"
            " sync_watermark VARCHAR(50),"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
    )
    op.create_table(
        "sync_operations",
        sa.Column("sequence_no", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("resource_node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("op_type", sa.String(length=20), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("vector_clock", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("sequence_no", name="pk_sync_operations"),
        sa.ForeignKeyConstraint(
            ["resource_node_id"],
            ["resource_nodes.id"],
            name="fk_sync_operations_resource_node_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "resource_node_id", "operation_id", name="uq_sync_operations_node_operation"
        ),
    )
    op.create_index(
        "ix_sync_operations_resource_node_id",
        "sync_operations",
        ["resource_node_id"],
        unique=False,
    )
    # Force a COMMIT so the just-created tables are visible to the
    # follow-on seed_hub.py that runs in a fresh process. Without this
    # the enclosing alembic transaction holds the DDL until exit and the
    # Hub boot script sees ``UndefinedTableError`` for ``hub_nodes``.
    bind.execute(sa.text("COMMIT"))


def downgrade() -> None:
    op.drop_index("ix_sync_operations_resource_node_id", table_name="sync_operations")
    op.drop_table("sync_operations")
