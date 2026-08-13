"""Create Knowledge Graph tables (NFM-838 Batch 2)

Revision ID: 011
Revises: 010
Create Date: 2026-07-07

Creates 3 tables:
- kg_nodes: Knowledge graph nodes (materials, properties, values, etc.)
- kg_edges: Directed edges with relation types and confidence scores
- kg_review_queue: Human review queue for low-confidence items

Per CTO spec §3 and ADR-NFM-817-2: relational tables with
Apache AGE graph view for Cypher-based path traversal.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: str | Sequence[str] | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create KG tables in foreign-key dependency order."""

    # =========================================================================
    # kg_nodes
    # =========================================================================
    op.create_table(
        "kg_nodes",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "entity_type",
            sa.String(100),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(1000),
            nullable=False,
        ),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "properties",
            sa.Text(),
            nullable=True,
            comment="JSON-encoded ontology-specific properties",
        ),
        sa.Column(
            "confidence_score",
            sa.Numeric(5, 4),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "sources.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "figure_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Provenance figure reference (optional)",
        ),
        sa.Column(
            "extraction_method",
            sa.String(50),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_kg_nodes_confidence",
        ),
        sa.CheckConstraint(
            "entity_type IN ("
            "'material', 'property', 'value', 'crystal_structure', "
            "'dataset', 'publication', 'author', 'experiment', "
            "'composition', 'defect_mechanism', 'other')",
            name="ck_kg_nodes_entity_type",
        ),
    )
    op.create_index("idx_kg_nodes_type", "kg_nodes", ["entity_type"])
    op.create_index("idx_kg_nodes_name", "kg_nodes", ["name"])
    op.create_index("idx_kg_nodes_source", "kg_nodes", ["source_document_id"])
    op.create_index("idx_kg_nodes_figure", "kg_nodes", ["figure_id"])

    # =========================================================================
    # kg_edges
    # =========================================================================
    op.create_table(
        "kg_edges",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "kg_nodes.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "kg_nodes.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "relation_type",
            sa.String(200),
            nullable=False,
        ),
        sa.Column("label", sa.String(300), nullable=True),
        sa.Column(
            "properties",
            sa.Text(),
            nullable=True,
            comment="JSON-encoded edge properties (weight, qualifiers)",
        ),
        sa.Column(
            "confidence_score",
            sa.Numeric(5, 4),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "sources.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "extraction_method",
            sa.String(50),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_kg_edges_confidence",
        ),
        sa.CheckConstraint(
            "source_id != target_id",
            name="ck_kg_edges_no_self_loop",
        ),
    )
    op.create_index("idx_kg_edges_source", "kg_edges", ["source_id"])
    op.create_index("idx_kg_edges_target", "kg_edges", ["target_id"])
    op.create_index("idx_kg_edges_type", "kg_edges", ["relation_type"])
    op.create_index(
        "idx_kg_edges_source_target",
        "kg_edges",
        ["source_id", "target_id"],
    )

    # =========================================================================
    # kg_review_queue
    # =========================================================================
    op.create_table(
        "kg_review_queue",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "item_type",
            sa.String(20),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="References kg_nodes.id or kg_edges.id",
        ),
        sa.Column(
            "confidence_score",
            sa.Numeric(5, 4),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "reviewer_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.String(50),
            nullable=True,
            comment="ISO timestamp of review completion",
        ),
        sa.Column(
            "original_data",
            sa.Text(),
            nullable=True,
            comment="JSON snapshot of original node/edge data",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "item_type IN ('node', 'edge')",
            name="ck_kg_review_queue_item_type",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'skipped')",
            name="ck_kg_review_queue_status",
        ),
    )
    op.create_index("idx_kg_review_status", "kg_review_queue", ["review_status"])
    op.create_index(
        "idx_kg_review_item",
        "kg_review_queue",
        ["item_type", "item_id"],
    )
    op.create_index(
        "idx_kg_review_confidence",
        "kg_review_queue",
        ["confidence_score"],
    )

    # =========================================================================
    # Apache AGE graph view (per ADR-NFM-817-2)
    # =========================================================================
    op.execute(
        """
        SELECT create_graph('nucmat_kg');
        """
    )
    op.execute(
        """
        SELECT * FROM cypher('nucmat_kg',
            $$
            CREATE (n:KGNode {
                id: '',
                entity_type: '',
                name: '',
                label: '',
                confidence_score: 0.0
            })
            CREATE (e:KGEdge {
                id: '',
                relation_type: '',
                label: '',
                confidence_score: 0.0
            })
            RETURN n, e
            $$
        ) AS (n, e);
        """
    )


def downgrade() -> None:
    """Drop KG tables and AGE graph in reverse order."""

    op.execute("SELECT drop_graph('nucmat_kg', true);")
    op.drop_table("kg_review_queue")
    op.drop_table("kg_edges")
    op.drop_table("kg_nodes")
