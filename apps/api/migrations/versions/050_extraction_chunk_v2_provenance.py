"""Add V2 provenance columns to extraction_chunks (NFM-2687).

Extends the extraction_chunks table created by migration 042
(``extraction_step_and_chunk``) with the columns required by the V2
strangler-fig pipeline:

  * ``step_name``        — V2 pipeline step identity (String(100))
  * ``source_span_hash`` — SHA-256 of (job_id, step_name, source_span)
                           used as the idempotency key for upsert
  * ``token_estimate``   — V2 token count (the existing ``token_count``
                           column is kept for V1 chunker compatibility)
  * ``metadata_``        — flexible V2 JSON metadata

Also adds a partial unique index
``ix_extraction_chunks_v2_idempotency`` on
``(job_id, step_name, source_span_hash)`` that is only enforced when
both ``step_name`` and ``source_span_hash`` are non-NULL. V1 rows
(which lack these columns) are not constrained, so the existing V1
chunker persistence path is unaffected.

V1 rows remain readable: every new column is nullable. The migration is
backward compatible — no data backfill is required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "050_extraction_chunk_v2_provenance"
down_revision: str | Sequence[str] | None = (
    "049_add_ontology_version_to_extraction_job"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add V2 columns and partial unique index to extraction_chunks."""
    op.add_column(
        "extraction_chunks",
        sa.Column("step_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "extraction_chunks",
        sa.Column("source_span_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_chunks",
        sa.Column("token_estimate", sa.Integer(), nullable=True),
    )
    op.add_column(
        "extraction_chunks",
        sa.Column("metadata_", JSONB, nullable=True),
    )
    op.create_index(
        "ix_extraction_chunks_v2_idempotency",
        "extraction_chunks",
        ["job_id", "step_name", "source_span_hash"],
        unique=True,
        postgresql_where=sa.text(
            "step_name IS NOT NULL AND source_span_hash IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Remove V2 columns and the partial unique index."""
    op.drop_index(
        "ix_extraction_chunks_v2_idempotency", table_name="extraction_chunks"
    )
    op.drop_column("extraction_chunks", "metadata_")
    op.drop_column("extraction_chunks", "token_estimate")
    op.drop_column("extraction_chunks", "source_span_hash")
    op.drop_column("extraction_chunks", "step_name")
