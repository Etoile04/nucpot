"""Add ontology_version_id FK to kg_entity_types and kg_relation_types (NFM-2873).

Phase 2.3 — version the ontology type registries so each entity/relation
type row belongs to a specific OntologyVersion instead of being a global
singleton.

Steps:
1. Add ``ontology_version_id`` (UUID, nullable, FK → ontology_versions.id)
   to both ``kg_entity_types`` and ``kg_relation_types``.
2. Backfill existing rows with the oldest published OntologyVersion.
3. Make the column NOT NULL after backfill (every type must have a version).

Reversible — downgrade drops both columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "055_add_ontology_version_id_to_type_tables"
down_revision: str | Sequence[str] | None = "053_align_extraction_gap_with_adr_nfm_2675"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ontology_version_id FK to kg_entity_types and kg_relation_types."""
    # --- kg_entity_types ---
    op.add_column(
        "kg_entity_types",
        sa.Column(
            "ontology_version_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="FK to the OntologyVersion this type belongs to.",
        ),
    )
    op.create_foreign_key(
        "fk_kg_entity_types_ontology_version_id",
        "kg_entity_types",
        "ontology_versions",
        ["ontology_version_id"],
        ["id"],
    )

    # --- kg_relation_types ---
    op.add_column(
        "kg_relation_types",
        sa.Column(
            "ontology_version_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="FK to the OntologyVersion this type belongs to.",
        ),
    )
    op.create_foreign_key(
        "fk_kg_relation_types_ontology_version_id",
        "kg_relation_types",
        "ontology_versions",
        ["ontology_version_id"],
        ["id"],
    )

    # --- Backfill: point existing rows at the earliest published version ---
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                v_id UUID;
            BEGIN
                SELECT id INTO v_id
                FROM ontology_versions
                WHERE status = 'published'
                ORDER BY created_at ASC
                LIMIT 1;

                IF v_id IS NULL THEN
                    SELECT id INTO v_id
                    FROM ontology_versions
                    ORDER BY created_at ASC
                    LIMIT 1;
                END IF;

                IF v_id IS NOT NULL THEN
                    UPDATE kg_entity_types SET ontology_version_id = v_id
                    WHERE ontology_version_id IS NULL;

                    UPDATE kg_relation_types SET ontology_version_id = v_id
                    WHERE ontology_version_id IS NULL;
                END IF;
            END;
            $$;
            """
        )
    )

    # --- Make NOT NULL now that all rows are backfilled ---
    op.alter_column("kg_entity_types", "ontology_version_id", nullable=False)
    op.alter_column("kg_relation_types", "ontology_version_id", nullable=False)


def downgrade() -> None:
    """Drop ontology_version_id from both type tables."""
    op.drop_constraint(
        "fk_kg_relation_types_ontology_version_id",
        "kg_relation_types",
        type_="foreignkey",
    )
    op.drop_column("kg_relation_types", "ontology_version_id")

    op.drop_constraint(
        "fk_kg_entity_types_ontology_version_id",
        "kg_entity_types",
        type_="foreignkey",
    )
    op.drop_column("kg_entity_types", "ontology_version_id")
