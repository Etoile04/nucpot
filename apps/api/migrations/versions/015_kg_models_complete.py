"""Complete KG model schema: figure_id, corpus, AGE sync, review queue, ontology map

Revision ID: 015
Revises: 014
Create Date: 2026-07-08

Bridges gap between migration 012/013/014 DDL and the current ORM models:
- Fix stale ck_kg_nodes_node_type: add missing 'Measurement' type
- Add figure_id FK to kg_nodes (spec §6.2)
- Add corpus_id, synced_to_graph, graph_synced_at to kg_nodes and kg_edges
  (multi-corpus support + Apache AGE graph sync tracking)
- Create kg_review_queue table (human review for low-confidence items)
- Create ontology_id_map table (external NVL ontology → internal KG node mapping)

NFM-855 — Phase 2B.1: KG Model Completion.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015"
down_revision: str | Sequence[str] | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply all KG model completion changes."""

    # =========================================================================
    # FIX: Stale ck_kg_nodes_node_type missing 'Measurement' (H1)
    # Migration 012 only had 5 node types; ORM added 'Measurement' later.
    # =========================================================================
    op.execute("""
        ALTER TABLE kg_nodes
            DROP CONSTRAINT IF EXISTS ck_kg_nodes_node_type,
            ADD CONSTRAINT ck_kg_nodes_node_type
                CHECK (node_type IN (
                    'Material', 'Property', 'Experiment',
                    'Condition', 'Publication', 'Measurement'
                ))
    """)

    # =========================================================================
    # ALTER kg_nodes: add figure_id, corpus_id, AGE sync columns
    # =========================================================================
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS figure_id UUID
                REFERENCES extraction_figures(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS corpus_id VARCHAR(100),
            ADD COLUMN IF NOT EXISTS synced_to_graph BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS graph_synced_at TIMESTAMPTZ
    """)

    # Index for figure_id lookups (material nodes linked to a figure)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_nodes_figure_id "
        "ON kg_nodes (figure_id)"
    )
    # Index for corpus-scoped queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_nodes_corpus_id "
        "ON kg_nodes (corpus_id)"
    )

    # =========================================================================
    # ALTER kg_edges: add corpus_id, AGE sync columns
    # =========================================================================
    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS corpus_id VARCHAR(100),
            ADD COLUMN IF NOT EXISTS synced_to_graph BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS graph_synced_at TIMESTAMPTZ
    """)

    # Index for corpus-scoped edge queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_edges_corpus_id "
        "ON kg_edges (corpus_id)"
    )

    # =========================================================================
    # TABLE: kg_review_queue
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS kg_review_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            item_type VARCHAR(20) NOT NULL,
            item_id UUID NOT NULL,
            review_reason TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewer_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ,

            CONSTRAINT ck_kg_review_queue_item_type
                CHECK (item_type IN ('entity', 'relation')),
            CONSTRAINT ck_kg_review_queue_status
                CHECK (status IN ('pending', 'approved', 'rejected', 'modified'))
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_review_queue_status "
        "ON kg_review_queue (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_review_queue_item "
        "ON kg_review_queue (item_id)"
    )

    # =========================================================================
    # TABLE: ontology_id_map
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS ontology_id_map (
            nvl_id VARCHAR(100) NOT NULL,
            corpus_id VARCHAR(100) NOT NULL,
            node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            graph_label VARCHAR(200),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            PRIMARY KEY (nvl_id, corpus_id),
            CONSTRAINT uq_ontology_id_map_nvl_corpus
                UNIQUE (nvl_id, corpus_id)
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ontology_id_map_node_id "
        "ON ontology_id_map (node_id)"
    )


def downgrade() -> None:
    """Revert all KG model completion changes."""
    op.execute("DROP TABLE IF EXISTS ontology_id_map CASCADE")
    op.execute("DROP TABLE IF EXISTS kg_review_queue CASCADE")

    # Drop kg_edges added columns
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS graph_synced_at")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS synced_to_graph")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS corpus_id")

    # Drop kg_nodes added columns
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS graph_synced_at")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS synced_to_graph")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS corpus_id")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS figure_id")

    # Drop indexes (safe even if columns were dropped above)
    op.execute("DROP INDEX IF EXISTS ix_kg_nodes_corpus_id")
    op.execute("DROP INDEX IF EXISTS ix_kg_nodes_figure_id")
    op.execute("DROP INDEX IF EXISTS ix_kg_edges_corpus_id")

    # Revert CHECK constraint to original 5 types (pre-015 state)
    op.execute("""
        ALTER TABLE kg_nodes
            DROP CONSTRAINT IF EXISTS ck_kg_nodes_node_type,
            ADD CONSTRAINT ck_kg_nodes_node_type
                CHECK (node_type IN (
                    'Material', 'Property', 'Experiment',
                    'Condition', 'Publication'
                ))
    """)
