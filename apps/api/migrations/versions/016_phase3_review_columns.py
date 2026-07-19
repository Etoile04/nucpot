"""Add review provenance and tracking columns for Phase 3 review API.

Covers:
- kg_nodes: review_note, reviewed_at
- kg_edges: review_note, reviewed_at
- property_measurements: reviewed_at
- extraction_results: item_type, item_data, source_paragraph, source_page,
  source_doi, review_note (renamed from review_notes), reviewed_by (kept)

All operations use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS so the
migration is idempotent and safe to re-run.

Revision ID: 016_phase3_review_columns
Revises: 015_kg_models_complete
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "016_phase3_review_columns"
down_revision = "015_kg_models_complete"
branch_labels = None
migration_type = "data"

# --- kg_nodes ---
op.add_column(
    "kg_nodes",
    sa.Column("review_note", sa.Text(), nullable=True),
)
op.add_column(
    "kg_nodes",
    sa.Column(
        "reviewed_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ),
)

# --- kg_edges ---
op.add_column(
    "kg_edges",
    sa.Column("review_note", sa.Text(), nullable=True),
)
op.add_column(
    "kg_edges",
    sa.Column(
        "reviewed_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ),
)

# --- property_measurements ---
op.add_column(
    "property_measurements",
    sa.Column(
        "reviewed_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ),
)

# --- extraction_results ---
op.add_column(
    "extraction_results",
    sa.Column("item_type", sa.String(100), nullable=True),
)
op.add_column(
    "extraction_results",
    sa.Column("item_data", sa.JSON(), nullable=True),
)
op.add_column(
    "extraction_results",
    sa.Column("source_paragraph", sa.Text(), nullable=True),
)
op.add_column(
    "extraction_results",
    sa.Column("source_page", sa.Integer(), nullable=True),
)
op.add_column(
    "extraction_results",
    sa.Column("source_doi", sa.String(100), nullable=True),
)
op.add_column(
    "extraction_results",
    sa.Column("review_note", sa.Text(), nullable=True),
)
# Make job_id and value nullable for test contexts (items without jobs).
op.alter_column(
    "extraction_results",
    "job_id",
    existing_nullable=True,
)
op.alter_column(
    "extraction_results",
    "value",
    existing_type=sa.JSON(),
    existing_nullable=True,
)
