"""Create M2 data submission 1+N architecture tables

Revision ID: 032_create_data_submission_tables
Revises: 031_seed_property_types
Create Date: 2026-07-30

NFM-2019: W1 of the M2 Data Submission 1+N architecture epic
(NFM-2018).  Creates the six core tables and the foreign keys
that enforce referential integrity across hub ↔ resource nodes,
upload sessions, and ingest logs.

Schema overview:

  hub_nodes
    The single central authority in the 1+N topology.
    Columns: id (UUID PK), name, api_endpoint, public_key,
    status, last_heartbeat, created_at, updated_at.

  resource_nodes
    Downstream sites registered under a hub.
    Columns: id (UUID PK), hub_node_id (FK → hub_nodes.id
    ON DELETE CASCADE), name, node_type, api_endpoint,
    public_key, status, last_heartbeat, offline_since,
    sync_watermark, created_at, updated_at.

  data_dna
    Cryptographic fingerprint for contributed records.
    Columns: id (UUID PK), record_type, record_id,
    dna_uuid (UNIQUE UUIDv4), sha256_hash, sm3_hash,
    created_at, updated_at.

  classification_levels
    Security labels (非密 / 内部 / 秘密) used by upload
    sessions and data DNA records.  Columns: id (UUID PK),
    label (UNIQUE), description, created_at, updated_at.

  upload_sessions
    Chunked file submission lifecycle.  Columns: id (UUID
    PK), resource_node_id (FK → resource_nodes.id ON DELETE
    CASCADE), file_name, total_size, chunk_size, total_chunks,
    uploaded_chunks, resume_token (UNIQUE), sha256_full, status,
    created_at, updated_at.

  ingest_logs
    Audit trail for upload/download operations.  Columns:
    id (UUID PK), resource_node_id (FK → resource_nodes.id
    ON DELETE CASCADE), hub_node_id (FK → hub_nodes.id
    ON DELETE SET NULL, NULLABLE), direction, record_count,
    data_size_bytes, status, error_detail, started_at,
    completed_at.

Tables are created in foreign key dependency order so the
upgrade is a single forward step with no ALTER TABLE
dependencies.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "032_create_data_submission_tables"
down_revision: str | Sequence[str] | None = "031_seed_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all six M2 data submission tables and their FKs."""

    # ------------------------------------------------------------------
    # hub_nodes
    # ------------------------------------------------------------------
    op.create_table(
        "hub_nodes",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
            comment="Hub node display name.",
        ),
        sa.Column(
            "api_endpoint",
            sa.String(length=500),
            nullable=False,
            comment="Base URL of the hub node API.",
        ),
        sa.Column(
            "public_key",
            sa.String(length=2000),
            nullable=True,
            comment="Public key for cryptographic verification.",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'active'"),
            comment="Operational status: active, inactive, suspended.",
        ),
        sa.Column(
            "last_heartbeat",
            sa.String(length=50),
            nullable=True,
            comment="ISO timestamp of last heartbeat from the hub.",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_hub_nodes"),
    )

    # ------------------------------------------------------------------
    # resource_nodes
    # ------------------------------------------------------------------
    op.create_table(
        "resource_nodes",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "hub_node_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owning hub node; CASCADE on hub delete.",
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
            comment="Resource node display name.",
        ),
        sa.Column(
            "node_type",
            sa.String(length=50),
            nullable=False,
            comment="Type: computing, storage, observatory.",
        ),
        sa.Column(
            "api_endpoint",
            sa.String(length=500),
            nullable=False,
            comment="Base URL of the resource node API.",
        ),
        sa.Column(
            "public_key",
            sa.String(length=2000),
            nullable=True,
            comment="Public key for cryptographic verification.",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'active'"),
            comment="Operational status: active, inactive, suspended.",
        ),
        sa.Column(
            "last_heartbeat",
            sa.String(length=50),
            nullable=True,
            comment="ISO timestamp of last heartbeat from the resource node.",
        ),
        sa.Column(
            "offline_since",
            sa.String(length=50),
            nullable=True,
            comment="ISO timestamp when the node transitioned to offline.",
        ),
        sa.Column(
            "sync_watermark",
            sa.String(length=50),
            nullable=True,
            comment="ISO timestamp of the last successfully synced batch.",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_resource_nodes"),
        sa.ForeignKeyConstraint(
            ["hub_node_id"],
            ["hub_nodes.id"],
            name="fk_resource_nodes_hub_node_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_resource_nodes_hub_node_id",
        "resource_nodes",
        ["hub_node_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # data_dna
    # ------------------------------------------------------------------
    op.create_table(
        "data_dna",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "record_type",
            sa.String(length=100),
            nullable=False,
            comment="Type of the record being fingerprinted.",
        ),
        sa.Column(
            "record_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="UUID of the source record being fingerprinted.",
        ),
        sa.Column(
            "dna_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="UUIDv4 content fingerprint.",
        ),
        sa.Column(
            "sha256_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex digest of the record content.",
        ),
        sa.Column(
            "sm3_hash",
            sa.String(length=64),
            nullable=True,
            comment="Optional SM3 hex digest (GB/T 32905).",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_data_dna"),
        sa.UniqueConstraint("dna_uuid", name="uq_data_dna_dna_uuid"),
    )
    op.create_index(
        "ix_data_dna_record_type",
        "data_dna",
        ["record_type"],
        unique=False,
    )
    op.create_index(
        "ix_data_dna_record_id",
        "data_dna",
        ["record_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_dna_sha256_hash",
        "data_dna",
        ["sha256_hash"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # classification_levels
    # ------------------------------------------------------------------
    op.create_table(
        "classification_levels",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "label",
            sa.String(length=50),
            nullable=False,
            comment="Contract label (非密, 内部, 秘密).",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Free-form description of the level.",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_classification_levels"),
        sa.UniqueConstraint("label", name="uq_classification_levels_label"),
    )

    # ------------------------------------------------------------------
    # upload_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "upload_sessions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "resource_node_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Resource node performing the upload; CASCADE on delete.",
        ),
        sa.Column(
            "file_name",
            sa.String(length=500),
            nullable=False,
            comment="Original file name as supplied by the resource node.",
        ),
        sa.Column(
            "total_size",
            sa.BigInteger(),
            nullable=False,
            comment="Total file size in bytes.",
        ),
        sa.Column(
            "chunk_size",
            sa.BigInteger(),
            nullable=False,
            comment="Size of each chunk in bytes.",
        ),
        sa.Column(
            "total_chunks",
            sa.BigInteger(),
            nullable=False,
            comment="Number of chunks the file was split into.",
        ),
        sa.Column(
            "uploaded_chunks",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Number of chunks successfully uploaded so far.",
        ),
        sa.Column(
            "resume_token",
            sa.String(length=64),
            nullable=True,
            comment="Opaque token used to resume a paused upload.",
        ),
        sa.Column(
            "sha256_full",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 of the fully reassembled file (set on completion).",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="Lifecycle status: pending, in_progress, completed, failed.",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_upload_sessions"),
        sa.UniqueConstraint("resume_token", name="uq_upload_sessions_resume_token"),
        sa.ForeignKeyConstraint(
            ["resource_node_id"],
            ["resource_nodes.id"],
            name="fk_upload_sessions_resource_node_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_upload_sessions_resource_node_id",
        "upload_sessions",
        ["resource_node_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # ingest_logs
    # ------------------------------------------------------------------
    op.create_table(
        "ingest_logs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "resource_node_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Originating resource node.",
        ),
        sa.Column(
            "hub_node_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Counter-party hub node; NULL when only the resource is known.",
        ),
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=False,
            comment="Flow direction: upload or download.",
        ),
        sa.Column(
            "record_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Number of records transferred.",
        ),
        sa.Column(
            "data_size_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Aggregate data size in bytes.",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="Lifecycle status: pending, in_progress, completed, failed.",
        ),
        sa.Column(
            "error_detail",
            sa.Text(),
            nullable=True,
            comment="Free-form error payload when status=failed.",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Wall-clock time the operation started.",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Wall-clock time the operation completed (NULL while in flight).",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingest_logs"),
        sa.ForeignKeyConstraint(
            ["resource_node_id"],
            ["resource_nodes.id"],
            name="fk_ingest_logs_resource_node_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hub_node_id"],
            ["hub_nodes.id"],
            name="fk_ingest_logs_hub_node_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_ingest_logs_resource_node_id",
        "ingest_logs",
        ["resource_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_ingest_logs_hub_node_id",
        "ingest_logs",
        ["hub_node_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop all six M2 data submission tables in reverse dependency order."""
    op.drop_index("ix_ingest_logs_hub_node_id", table_name="ingest_logs")
    op.drop_index("ix_ingest_logs_resource_node_id", table_name="ingest_logs")
    op.drop_table("ingest_logs")

    op.drop_index(
        "ix_upload_sessions_resource_node_id", table_name="upload_sessions"
    )
    op.drop_table("upload_sessions")

    op.drop_table("classification_levels")

    op.drop_index("ix_data_dna_sha256_hash", table_name="data_dna")
    op.drop_index("ix_data_dna_record_id", table_name="data_dna")
    op.drop_index("ix_data_dna_record_type", table_name="data_dna")
    op.drop_table("data_dna")

    op.drop_index("ix_resource_nodes_hub_node_id", table_name="resource_nodes")
    op.drop_table("resource_nodes")

    op.drop_table("hub_nodes")
