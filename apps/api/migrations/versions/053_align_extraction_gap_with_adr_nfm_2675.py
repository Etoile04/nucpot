"""Align extraction_gaps schema with ADR-NFM-2675 Section 1.

Schema changes
-------------
1. Add ``literature_id`` UUID FK to ``data_sources(id)`` (nullable first;
   a separate data-migration will backfill from
   ``extraction_chunk -> extraction_job.corpus_id -> data_sources.doi``).
2. Replace ``ontology_version_id`` UUID FK -> ``ontology_version`` TEXT NOT
   NULL (populated from ``ontology_versions.version`` for existing rows).
3. Replace the 3-tuple UNIQUE ``(ontology_version_id, entity_type, property)``
   with a 5-tuple UNIQUE ``(ontology_version, entity_type, property,
   literature_id, chunk_id)`` per ADR Section 1.

Upgrade path (additive on the wire -- existing rows remain queryable):
    a. Add ``literature_id`` nullable + ``ontology_version`` nullable.
    b. Backfill ``ontology_version`` from ``ontology_versions.version``.
    c. Swap: drop old UNIQUE + ``ontology_version_id`` indexes, drop
       ``ontology_version_id`` FK, drop ``ontology_version_id`` column,
       rename ``ontology_version`` to canonical, set NOT NULL, add new
       5-tuple UNIQUE.

Downgrade path:
    a. Re-create ``ontology_version_id`` nullable column.
    b. Backfill from ``ontology_versions.id`` matching ``.version``.
    c. Re-create old UNIQUE, indexes, and FK.
    d. Drop ``ontology_version`` TEXT + ``literature_id`` FK.

ADR reference: ADR-NFM-2675 Section 1.
Issue: NFM-2697-T1 (NFM-2731).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "053_align_extraction_gap_with_adr_nfm_2675"
down_revision: str | Sequence[str] | None = "052_add_datasource_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. Add new columns (nullable -- backfill next).
    # Idempotent: prod DB may have run part of this before the
    # alembic_version was rolled back.
    # ---------------------------------------------------------------
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ADD COLUMN IF NOT EXISTS literature_id UUID REFERENCES data_sources(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ADD COLUMN IF NOT EXISTS ontology_version_tmp VARCHAR(50)"
    )

    # ---------------------------------------------------------------
    # 2. Backfill ontology_version_tmp from ontology_versions.version.
    # Skip rows that already have ontology_version_tmp populated.
    # ---------------------------------------------------------------
    op.execute(
        """
        UPDATE extraction_gaps eg
        SET ontology_version_tmp = ov.version
        FROM ontology_versions ov
        WHERE eg.ontology_version_id = ov.id
          AND eg.ontology_version_tmp IS NULL
        """
    )

    # ---------------------------------------------------------------
    # 3. Swap columns: drop old, rename new
    # ---------------------------------------------------------------
    # Drop old indexes first. Use raw SQL with IF EXISTS so this
    # migration is idempotent against production databases whose
    # indexes may have been created with slightly different names
    # (e.g. SQLAlchemy create_all() vs alembic apply -- prod has
    # `uq_extraction_gaps_ov_entity_prop` not
    # `ix_extraction_gaps_ov_entity_property`).
    op.execute("DROP INDEX IF EXISTS ix_extraction_gaps_ontology_version_id")
    op.execute("DROP INDEX IF EXISTS ix_extraction_gaps_ov_entity_property")
    # Also drop the prod-named unique index in case alembic-create never ran
    op.execute("DROP INDEX IF EXISTS uq_extraction_gaps_ov_entity_prop")

    # Drop FK constraint on ontology_version_id, then the column.
    # Both idempotent: prod may already lack the FK if a prior partial
    # upgrade removed it before alembic_version was rolled back.
    op.execute(
        "ALTER TABLE extraction_gaps "
        "DROP CONSTRAINT IF EXISTS extraction_gaps_ontology_version_id_fkey"
    )
    op.execute("ALTER TABLE extraction_gaps DROP COLUMN IF EXISTS ontology_version_id")

    # Rename temporary column to canonical name
    op.execute(
        "ALTER TABLE extraction_gaps "
        "RENAME COLUMN ontology_version_tmp TO ontology_version"
    )
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ALTER COLUMN ontology_version SET NOT NULL"
    )

    # ---------------------------------------------------------------
    # 4. Add new indexes and UNIQUE constraint
    # ---------------------------------------------------------------
    # Index on ontology_version (TEXT) for recall/coverage queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_extraction_gaps_ontology_version "
        "ON extraction_gaps (ontology_version)"
    )

    # Index on literature_id for per-literature recall queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_extraction_gaps_literature_id "
        "ON extraction_gaps (literature_id)"
    )

    # 5-tuple UNIQUE constraint per ADR Section 1
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_extraction_gaps_ov_entity_prop_lit_chunk "
        "ON extraction_gaps (ontology_version, entity_type, property, literature_id, chunk_id)"
    )


def downgrade() -> None:
    # ---------------------------------------------------------------
    # 1. Re-create ontology_version_id column (nullable)
    # ---------------------------------------------------------------
    op.add_column(
        "extraction_gaps",
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            nullable=True,
            comment="Ontology version FK (restored from downgrade).",
        ),
    )

    # Backfill ontology_version_id from ontology_versions matching version
    op.execute(
        """
        UPDATE extraction_gaps eg
        SET ontology_version_id = ov.id
        FROM ontology_versions ov
        WHERE eg.ontology_version = ov.version
        """
    )

    # ---------------------------------------------------------------
    # 2. Drop new columns and indexes
    # ---------------------------------------------------------------
    op.drop_index(
        "ix_extraction_gaps_ov_entity_prop_lit_chunk",
        table_name="extraction_gaps",
    )
    op.drop_index("ix_extraction_gaps_literature_id", table_name="extraction_gaps")
    op.drop_index("ix_extraction_gaps_ontology_version", table_name="extraction_gaps")

    # Drop ontology_version TEXT column
    op.drop_column("extraction_gaps", "ontology_version")

    # Drop literature_id FK and column
    op.drop_constraint(
        "extraction_gaps_literature_id_fkey",
        "extraction_gaps",
        type_="foreignkey",
    )
    op.drop_column("extraction_gaps", "literature_id")

    # ---------------------------------------------------------------
    # 3. Restore ontology_version_id FK and NOT NULL + old indexes
    # ---------------------------------------------------------------
    op.create_foreign_key(
        "extraction_gaps_ontology_version_id_fkey",
        "extraction_gaps",
        "ontology_versions",
        ["ontology_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "extraction_gaps",
        "ontology_version_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    # Restore old unique constraint (3-tuple)
    op.create_index(
        "ix_extraction_gaps_ov_entity_property",
        "extraction_gaps",
        ["ontology_version_id", "entity_type", "property"],
        unique=True,
    )
    # Restore old recall-metrics index
    op.create_index(
        "ix_extraction_gaps_ontology_version_id",
        "extraction_gaps",
        ["ontology_version_id"],
    )
