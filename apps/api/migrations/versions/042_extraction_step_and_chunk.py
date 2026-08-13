"""Create extraction_steps and extraction_chunks tables (NFM-2567-T2).

ExtractionStep tracks per-step status, timing, and error state within an
extraction pipeline run.  ExtractionChunk stores the text chunks produced
by the chunking step — the atomic units fed to downstream extraction.

Both tables reference extraction_jobs.id via FK and are indexed on job_id
for query performance.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "042_extraction_step_and_chunk"
down_revision: str | Sequence[str] | None = "040_create_sync_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create extraction_steps and extraction_chunks tables."""
    op.create_table(
        "extraction_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("extraction_jobs.id"), nullable=False, comment="Parent extraction job this step belongs to."),
        sa.Column("step_type", sa.String(50), nullable=False, comment="chunk | extract | map | quality_gate | gap_scan"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", comment="pending | running | completed | failed | skipped"),
        sa.Column("input_hash", sa.Text, nullable=True, comment="Input fingerprint for skip detection on repeated runs."),
        sa.Column("output_id", UUID(as_uuid=True), nullable=True, comment="Reference to the product artifact this step produced."),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True, comment="Last failure reason when status='failed'."),
        sa.Column("metadata_", sa.dialects.postgresql.JSONB, nullable=True, comment="Arbitrary step metadata."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_extraction_steps_job_id", "extraction_steps", ["job_id"])

    op.create_table(
        "extraction_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("extraction_jobs.id"), nullable=False, comment="Parent extraction job that produced this chunk."),
        sa.Column("source_reference", sa.Text, nullable=True, comment="Source identifier (e.g. page number, section heading)."),
        sa.Column("content", sa.Text, nullable=False, comment="The chunk text produced by the chunker."),
        sa.Column("source_span", sa.dialects.postgresql.JSONB, nullable=True, comment='Source file offsets as {"start": int, "end": int}.'),
        sa.Column("chunk_index", sa.Integer, nullable=False, comment="Sequential index of this chunk within the job."),
        sa.Column("token_count", sa.Integer, nullable=True, comment="Estimated token count for downstream batching."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_extraction_chunks_job_id", "extraction_chunks", ["job_id"])


def downgrade() -> None:
    """Drop extraction_chunks and extraction_steps tables."""
    op.drop_index("ix_extraction_chunks_job_id", table_name="extraction_chunks")
    op.drop_table("extraction_chunks")
    op.drop_index("ix_extraction_steps_job_id", table_name="extraction_steps")
    op.drop_table("extraction_steps")
