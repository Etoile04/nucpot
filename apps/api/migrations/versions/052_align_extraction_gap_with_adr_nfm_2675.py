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

revision: str = "052_align_extraction_gap_with_adr_nfm_2675"
down_revision: str | Sequence[str] | None = "051_extraction_job_orchestration_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. Add new columns (nullable -- backfill next)
    # ---------------------------------------------------------------
    op.add_column(
        "extraction_gaps",
        sa.Column(
            "literature_id",
            sa.Uuid(),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
            nullable=True,
            comment="Literature (data_sources) row this gap was detected in.",
        ),
    )
    op.add_column(
        "extraction_gaps",
        sa.Column(
            "ontology_version_tmp",
            sa.String(50),
            nullable=True,
            comment="Temporary column for backfill.",
        ),
    )

    # ---------------------------------------------------------------
    # 2. Backfill ontology_version_tmp from ontology_versions.version
    # ---------------------------------------------------------------
    op.execute(
        """
        UPDATE extraction_gaps eg
        SET ontology_version_tmp = ov.version
        FROM ontology_versions ov
        WHERE eg.ontology_version_id = ov.id
        """
    )

    # ---------------------------------------------------------------
    # 3. Swap columns: drop old, rename new
    # ---------------------------------------------------------------
    # Drop old indexes first (they reference ontology_version_id)
    op.drop_index("ix_extraction_gaps_ontology_version_id", table_name="extraction_gaps")
    op.drop_index("ix_extraction_gaps_ov_entity_property", table_name="extraction_gaps")

    # Drop FK constraint on ontology_version_id, then the column
    op.drop_constraint(
        "extraction_gaps_ontology_version_id_fkey",
        "extraction_gaps",
        type_="foreignkey",
    )
    op.drop_column("extraction_gaps", "ontology_version_id")

    # Rename temporary column to canonical name
    op.alter_column(
        "extraction_gaps",
        "ontology_version_tmp",
        new_column_name="ontology_version",
        existing_type=sa.String(50),
        nullable=False,
        comment="Ontology version semver string, e.g. v2.1.0.",
    )

    # ---------------------------------------------------------------
    # 4. Add new indexes and UNIQUE constraint
    # ---------------------------------------------------------------
    # Index on ontology_version (TEXT) for recall/coverage queries
    op.create_index(
        "ix_extraction_gaps_ontology_version",
        "extraction_gaps",
        ["ontology_version"],
    )

    # Index on literature_id for per-literature recall queries
    op.create_index(
        "ix_extraction_gaps_literature_id",
        "extraction_gaps",
        ["literature_id"],
    )

    # 5-tuple UNIQUE constraint per ADR Section 1
    op.create_index(
        "ix_extraction_gaps_ov_entity_prop_lit_chunk",
        "extraction_gaps",
        ["ontology_version", "entity_type", "property", "literature_id", "chunk_id"],
        unique=True,
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
