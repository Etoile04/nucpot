"""Create the missing ``kg_entity_types`` and ``kg_relation_types`` tables.

NFM-3364 — hotfix: production DB was bootstrapped via a partial
``Base.metadata.create_all()`` and is missing these two ontology
registry tables. Ontology loading on every request then raises
``UndefinedTableError`` (table=kg_entity_types), which cascades
into an ``InFailedSQLTransactionError`` from the NFM-3322 savepoint
wrapper.

Schema matches the ORM models in
``nfm_db.models.ontology.KEntityType`` and ``KRelationType``:

* ``kg_entity_types``: id, name (unique), label_template,
  required_properties (JSONArray), description, created_at, updated_at
* ``kg_relation_types``: id, name (unique), source_types,
  target_types (JSONArray), properties_schema (JSONB), description,
  created_at, updated_at

The migration is intentionally idempotent (CREATE TABLE IF NOT EXISTS)
so that staging environments where the tables already exist via
``create_all`` remain green. The downstream migration 055 (NFM-2873-T1)
adds the ``ontology_version_id`` FK column and is rewired to chain
off this revision.

Downgrade drops the tables in reverse FK-dependent order (relations
first, since the FK lives on neither table here). The memory of this
revision is removed by Alembic automatically.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "057_create_kg_entity_and_relation_type_tables"
down_revision: str | Sequence[str] | None = "053_align_extraction_gap_with_adr_nfm_2675"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``kg_entity_types`` and ``kg_relation_types`` if missing."""

    # --- kg_entity_types (ontology node type registry) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_entity_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(50) NOT NULL,
            label_template VARCHAR(200),
            required_properties TEXT[],
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_kg_entity_types_name UNIQUE (name)
        )
        """
    )
    # The unique constraint above already creates a b-tree index on
    # ``name``; an explicit unique index is left as documentation for
    # operators looking at pg_indexes.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_entity_types_name "
        "ON kg_entity_types (name)"
    )

    # --- kg_relation_types (ontology edge type registry) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_relation_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            source_types TEXT[],
            target_types TEXT[],
            properties_schema JSONB,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_kg_relation_types_name UNIQUE (name)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_relation_types_name "
        "ON kg_relation_types (name)"
    )


def downgrade() -> None:
    """Drop both tables. No FKs exist yet — 055 is rewired to chain off here."""

    op.execute("DROP TABLE IF EXISTS kg_relation_types CASCADE")
    op.execute("DROP TABLE IF EXISTS kg_entity_types CASCADE")
