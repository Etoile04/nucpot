"""Create kg_nodes and kg_edges tables with performance indexes

Revision ID: 012
Revises: 011
Create Date: 2026-07-06 12:00:00.000000

Creates the core Knowledge Graph storage tables:
- kg_nodes: entity nodes (Material, Property, Experiment, Condition, Publication)
- kg_edges: directed relationships between nodes

NFM-714 — Phase 2B.1: Graph storage foundation per CTO ADR-1/ADR-3.

Indexes:
- ix_kg_nodes_type: lookup by entity type
- ix_kg_nodes_label_trgm: GIN trigram index for fuzzy name search
- ix_kg_edges_source: edges from a node
- ix_kg_edges_target: edges to a node
- ix_kg_edges_relation: edges by relation type
- ix_kg_edges_source_relation: composite for common graph traversals
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: str | Sequence[str] | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create kg_nodes and kg_edges tables with indexes."""

    # =========================================================================
    # EXTENSION: pg_trgm for fuzzy label matching
    # =========================================================================
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # =========================================================================
    # TABLE: kg_nodes
    # =========================================================================
    op.execute("""
        CREATE TABLE kg_nodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            node_type VARCHAR(50) NOT NULL,
            label VARCHAR(500) NOT NULL,
            aliases TEXT,
            properties JSONB DEFAULT '{}',
            confidence FLOAT DEFAULT 1.0,
            source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_kg_nodes_node_type
                CHECK (node_type IN ('Material', 'Property', 'Experiment',
                                     'Condition', 'Publication')),
            CONSTRAINT ck_kg_nodes_status
                CHECK (status IN ('active', 'merged', 'deprecated',
                                  'pending_review')),
            CONSTRAINT ck_kg_nodes_confidence
                CHECK (confidence >= 0.0 AND confidence <= 1.0)
        )
    """)

    # =========================================================================
    # TABLE: kg_edges
    # =========================================================================
    op.execute("""
        CREATE TABLE kg_edges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            target_node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            relation_type VARCHAR(100) NOT NULL,
            properties JSONB DEFAULT '{}',
            confidence FLOAT DEFAULT 1.0,
            source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_kg_edges_confidence
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CONSTRAINT uq_kg_edges_source_target_relation
                UNIQUE (source_node_id, target_node_id, relation_type)
        )
    """)

    # =========================================================================
    # INDEXES: kg_nodes
    # =========================================================================
    op.execute("CREATE INDEX ix_kg_nodes_type ON kg_nodes (node_type)")
    op.execute("""
        CREATE INDEX ix_kg_nodes_label_trgm
        ON kg_nodes USING gin (label gin_trgm_ops)
    """)

    # =========================================================================
    # INDEXES: kg_edges
    # =========================================================================
    op.execute("CREATE INDEX ix_kg_edges_source ON kg_edges (source_node_id)")
    op.execute("CREATE INDEX ix_kg_edges_target ON kg_edges (target_node_id)")
    op.execute("CREATE INDEX ix_kg_edges_relation ON kg_edges (relation_type)")
    op.execute(
        "CREATE INDEX ix_kg_edges_source_relation "
        "ON kg_edges (source_node_id, relation_type)"
    )


def downgrade() -> None:
    """Drop kg_edges and kg_nodes in FK-dependent order."""
    op.execute("DROP TABLE IF EXISTS kg_edges CASCADE")
    op.execute("DROP TABLE IF EXISTS kg_nodes CASCADE")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
