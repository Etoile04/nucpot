"""Add ontology_version_id FK to kg_entity_types and kg_relation_types (NFM-2873-T1).

Adds a nullable ``ontology_version_id`` column (UUID, FK -> ontology_versions.id)
to both type registry tables so each row records which OntologyVersion it
belongs to.

Existing rows are backfilled to the earliest published OntologyVersion
(seed version 0.1.0 from migration 044).  If no published version exists
the backfill is a no-op and the column stays NULL for those rows.

Reversible: downgrade drops both columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "055_add_ontology_version_fk_to_type_tables"
# NFM-3364: rewired to chain off 057, which creates the underlying
# tables if they are missing (production DB was bootstrapped via a
# partial ``Base.metadata.create_all()`` and lacked kg_entity_types
# / kg_relation_types entirely).  055's ADD COLUMN statements would
# otherwise fail with UndefinedTableError on prod.
down_revision: str | Sequence[str] | None = "057_create_kg_entity_and_relation_type_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_TARGET = "ontology_versions.id"

# SQL subquery that resolves to the earliest published OntologyVersion PK.
# On a cold DB (no published version) the subquery returns NULL and the
# UPDATE becomes a no-op -- the column stays nullable.
_BACKFILL_ID_SUBQUERY = (
    "SELECT id FROM ontology_versions "
    "WHERE status = 'published' "
    "ORDER BY created_at ASC "
    "LIMIT 1"
)


def upgrade() -> None:
    """Add ontology_version_id to kg_entity_types and kg_relation_types."""

    # --- Schema: add nullable FK columns (no table rewrite) ---
    op.add_column(
        "kg_entity_types",
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey(FK_TARGET),
            nullable=True,
            comment="FK to the OntologyVersion this type belongs to.",
        ),
    )
    op.add_column(
        "kg_relation_types",
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey(FK_TARGET),
            nullable=True,
            comment="FK to the OntologyVersion this type belongs to.",
        ),
    )

    # --- Data: backfill existing rows to earliest published version ---
    op.execute(
        f"UPDATE kg_entity_types "
        f"SET ontology_version_id = ({_BACKFILL_ID_SUBQUERY}) "
        f"WHERE ontology_version_id IS NULL"
    )
    op.execute(
        f"UPDATE kg_relation_types "
        f"SET ontology_version_id = ({_BACKFILL_ID_SUBQUERY}) "
        f"WHERE ontology_version_id IS NULL"
    )


def downgrade() -> None:
    """Drop ontology_version_id from both type tables."""
    op.drop_column("kg_relation_types", "ontology_version_id")
    op.drop_column("kg_entity_types", "ontology_version_id")
