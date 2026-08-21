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
    """No-op (NFM-3383).

    This revision sits on the dead fork sibling of 012 — both fork from 010
    and 020 merges them. 012 (raw-SQL variant) is the revision that actually
    built kg_nodes/kg_edges/kg_review_queue on every real environment; this
    file's op.create_table chain never ran anywhere:

    * its FKs targeted a ``sources`` table that no migration ever created
      (009 creates ``data_sources``), so on a clean database it aborted the
      whole chain with ``UndefinedTableError: relation "sources" does not
      exist`` — exactly what the CI schema-drift guard catches;
    * it also requires the Apache AGE extension, which prod does not carry;
    * its kg_nodes schema (entity_type/name/confidence_score) is NOT the
      schema anything downstream expects — 012's (node_type/label/
      confidence) is, and 018's constraint updates index into those.

    Making it a hard no-op (rather than guarded-create) is the correct
    reconciliation: on a clean database the chain then goes 011(no-op) ->
    ... -> 022 (which stub-creates the 012-schema kg tables when the 012
    fork hasn't run yet) -> ... -> 012 (guard: already exists, skip) ->
    014/018 reconcile columns. On any database that already ran the chain,
    this is trivially a no-op.
    """
    return



def downgrade() -> None:
    """Drop KG tables and AGE graph in reverse order."""

    bind = op.get_bind()
    age_installed = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'age'")
    ).scalar()
    if age_installed:
        op.execute("SELECT drop_graph('nucmat_kg', true);")
    op.drop_table("kg_review_queue")
    op.drop_table("kg_edges")
    op.drop_table("kg_nodes")
