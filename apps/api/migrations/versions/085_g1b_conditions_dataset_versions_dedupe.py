"""G1-B schema — conditions JSONB + ``dataset_versions`` + ``dedupe_key``.

Revision ID: 085_g1b_conditions_dataset_versions_dedupe
Revises: 084_potentials_list_partial_index
Create Date: 2026-09-10

NFM-4548 (G1-B, child of NFM-4536 / NFM-4535)
=============================================

Implements the three schema deliverables named in the ticket:

1. ``property_measurements`` measurement-condition expansion — the five
   high-frequency condition keys are promoted to fixed columns and every
   remaining open key lands in a ``conditions`` JSONB bag
   (ADR-016 §2.4, ADR-017 §2.5, spec §3.1).
2. ``dataset_versions`` — the snapshot-style version table (ADR-017 §2.3,
   spec §3.3).  **AC-6.**
3. ``dedupe_key`` — composite uniqueness so a re-extraction of an
   already-ingested source stops minting duplicate rows
   (ADR-017 §2.6).  **AC-9.**

This migration is **purely additive DDL**.  It deletes no rows, drops no
existing column, and leaves every pre-existing constraint in place.

Design decisions worth reviewing
--------------------------------

**(a) ``conditions`` lands on ``property_measurements``, not on
``measurement_conditions``.**  ADR-016 §2.4 words the fallback bag as
``measurement_conditions.conditions``, but spec §3.1 lists ``conditions``
as a *row-level* field of the ``property_measurements`` contract and the
NFM-4548 ticket title says ``property_measurements.conditions``.  Two of
the three sources agree, and the row-level placement is the one that
actually works: ``measurement_conditions`` is **1-to-many** from
``property_measurements`` (``models/property.py`` —
``cascade="all, delete-orphan"``), so a bag hung off that table cannot
participate in a per-row dedupe key.  We therefore put the bag on the
measurement row.  ``measurement_conditions`` is left fully intact as the
legacy read path (ADR-017 §4.2 dual-read period); nothing is migrated
destructively.

**(b) Legacy condition values are backfilled under explicit ``legacy_*``
keys, not into the new typed columns.**  ``measurement_conditions.temperature``
is ``NUMERIC(10,2)`` with **no recorded unit**, and ``pressure`` likewise.
The new columns are unit-bearing (``temp_k``, ``pressure_gpa``).  Silently
copying an unlabelled number into a Kelvin/GPa column would fabricate a
unit conversion we cannot substantiate, so the backfill preserves the raw
legacy rows losslessly under ``conditions->'legacy_conditions'`` and
leaves the typed columns NULL for legacy rows.  Backfilling the typed
columns is a separate, evidence-driven task once the source units are
established.

**(c) ``method`` already exists** on ``property_measurements``
(``VARCHAR(100) NOT NULL DEFAULT ''``, added by migration 033 for the
NFM-2032 4-tuple).  It is one of ADR-016 §2.4's five high-frequency keys,
so this migration adds only the other four and reuses the existing column.

**(d) ``phase`` is deliberately NOT a column.**  ADR-017 §2.5 is explicit:
``phase`` belongs in ``conditions.phase`` and must not touch
``materials.crystal_structure``.  It is an open JSONB key here.

**(e) ``value_hash`` and ``dedupe_key`` are GENERATED ALWAYS ... STORED.**
The precedent column ``conditions_hash`` is application-computed, and that
is precisely how it drifted (migration 033's docstring documents legacy
rows stranded with ``conditions_hash = NULL`` and therefore unmatchable on
re-ingest).  A generated column cannot drift: Postgres recomputes it on
every INSERT/UPDATE, no ingest path can forget it, and ``ON CONFLICT
(dedupe_key)`` upserts work unchanged.

**(f) The dedupe tuple includes ``conditions_hash`` and ``method``, which
ADR-017 §2.6's literal 4-tuple omits.**  §2.6 states the key as
``(dataset_id, property_type_id, source_id, value_hash)`` but its very next
bullet requires that *"有条件差异保留各行 … 即使数值相同也不合并"* — rows
differing only in conditions must survive as distinct rows.  A key that
excludes conditions cannot express that; the literal 4-tuple would merge a
300 K and an 800 K measurement of the same value from the same source.  We
implement the **behaviour** §2.6 mandates by folding ``conditions_hash``
and ``method`` into the key.  This is a strict superset of the literal
tuple, so it cannot merge anything the literal tuple would keep apart, and
AC-9 is unaffected (a re-extraction reproduces identical conditions, so the
key collides exactly as intended).  Flagged for architectural confirmation
in the NFM-4548 thread.

**(g) The unique index is PARTIAL: ``WHERE dataset_version_id IS NOT NULL``.**
ADR-017 §4.1 requires the pre-existing Owen 92 rows to be **retained** as a
comparison cohort until the re-ingest pilot replaces them.  Those rows are
duplicates under the new key, so an unconditional UNIQUE index would fail
to build on staging and prod.  Scoping the constraint to rows that carry a
``dataset_version_id`` means legacy rows are untouched and every row
written through the new versioned path is deduped — which is exactly what
AC-9 asks for.

**(h) ``uq_pm_dedup`` is intentionally left in place, and the new key is not
redundant with it.**  Migration 070 and ``extraction_to_db_mapper.py`` both
issue ``ON CONFLICT (dataset_id, property_type_id, conditions_hash, method)
DO NOTHING``; dropping the index those clauses bind to would break the live
ingest path at runtime.  The two keys therefore coexist for the ADR-017 §4.2
dual-read period.

The new key earns its place by closing a hole the old one leaks.
``uq_pm_dedup`` is a plain btree UNIQUE over a **nullable** column
(``conditions_hash``), and Postgres treats NULLs as mutually distinct in a
unique index — so any number of otherwise-identical rows with
``conditions_hash IS NULL`` slip through.  That is not hypothetical: it is
the exact shape migration 033's docstring records for the legacy cohort
(*"Legacy rows had conditions_hash = NULL and therefore could not be matched
on subsequent ingest"*), and it is the shape the Owen duplicates take.
``dedupe_key`` folds every component through ``coalesce(..., '')`` before
hashing, so NULL and NULL collide as they should.  Verified on Postgres 16:
two versioned rows identical except for a NULL ``conditions_hash`` are
accepted by ``uq_pm_dedup`` and rejected by ``uq_pm_dedupe_key``.

Note also that ``uq_pm_dedup`` omits ``source_id``, so today it *wrongly*
merges two distinct sources contributing the same property under the same
conditions — the opposite failure.  ADR-017 §2.6 wants those kept apart.
Retiring the old 4-tuple in favour of ``dedupe_key`` belongs to the
ingest-path ticket that moves the mapper onto ``ON CONFLICT (dedupe_key)``;
until then the stricter old key wins on that axis.

**(i) The Owen 92-row scenario needs this migration's *other* half too.**
The Owen duplicates were *"同值重复 13 行分散于 10 个不同 dataset"*
(ADR-017 §1.1) — they differ in ``dataset_id``, so no key rooted at
``dataset_id`` can collapse them, this one included.  What actually stops
them is identity resolution: ``datasets.literature_doi`` /
``literature_content_hash`` (added above, ADR-017 §2.5) force a re-ingest of
the same paper onto the *same* dataset, and only then does ``dedupe_key``
collapse the rows within it.  AC-9 is satisfied by the two halves together;
neither is sufficient alone.

Reversibility
-------------
``downgrade()`` drops exactly what ``upgrade()`` added, in reverse
dependency order.  Because the migration adds no data and destroys none,
downgrade is lossless apart from the derived/backfilled ``conditions`` bag,
whose source rows remain in ``measurement_conditions``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "085_g1b_conditions_dataset_versions_dedupe"
down_revision: str | Sequence[str] | None = "084_potentials_list_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ADR-017 §2.3 — snapshot version lifecycle.
_DATASET_VERSION_STATUSES = ("draft", "released", "rolled_back", "superseded")

# The dedupe tuple.  Kept as a module constant so the migration test can
# assert the exact component list without re-parsing SQL.  See decision (f).
_DEDUPE_COMPONENTS = (
    "dataset_id",
    "property_type_id",
    "source_id",
    "value_hash",
    "conditions_hash",
    "method",
)

# Hash of the measured value itself (decision (e)).  ``value_scalar`` and the
# range/uncertainty columns are NUMERIC(20,15) so their ``::text`` rendering
# has a fixed scale and is stable across rows.  ``unit_id`` participates
# because the same number under a different unit is a different measurement.
_VALUE_HASH_SQL = """
md5(
    coalesce(value_scalar::text, '')     || '|' ||
    coalesce(value_min::text, '')        || '|' ||
    coalesce(value_max::text, '')        || '|' ||
    coalesce(value_expression, '')       || '|' ||
    coalesce(value_list::text, '')       || '|' ||
    coalesce(value_text, '')             || '|' ||
    coalesce(uncertainty::text, '')      || '|' ||
    coalesce(unit_id::text, '')
)
"""

# Postgres forbids one generated column referencing another, so the
# value-hash expression is inlined rather than referenced by name.
_DEDUPE_KEY_SQL = f"""
md5(
    coalesce(dataset_id::text, '')       || '|' ||
    coalesce(property_type_id::text, '') || '|' ||
    coalesce(source_id::text, '')        || '|' ||
    {_VALUE_HASH_SQL.strip()}            || '|' ||
    coalesce(conditions_hash, '')        || '|' ||
    coalesce(method, '')
)
"""


def upgrade() -> None:
    _upgrade_conditions()
    _upgrade_dataset_identity()
    _upgrade_dataset_versions()
    _upgrade_dedupe_key()


def downgrade() -> None:
    _downgrade_dedupe_key()
    _downgrade_dataset_versions()
    _downgrade_dataset_identity()
    _downgrade_conditions()


# ---------------------------------------------------------------------------
# 1. conditions — high-frequency keys promoted to columns + JSONB fallback
#    ADR-016 §2.4 / ADR-017 §2.5 / spec §3.1
# ---------------------------------------------------------------------------


def _upgrade_conditions() -> None:
    op.add_column(
        "property_measurements",
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Open measurement conditions (ADR-016 §2.4). High-frequency "
                "keys are promoted to sibling columns; every other key — "
                "including 'phase' per ADR-017 §2.5 — lives here."
            ),
        ),
    )

    # The four high-frequency keys that do not already exist as columns.
    # 'method' is deliberately absent: migration 033 already added it. See (c).
    op.add_column(
        "property_measurements",
        sa.Column("simulation_method", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "property_measurements",
        sa.Column("model_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "property_measurements",
        sa.Column(
            "temp_k",
            sa.Numeric(precision=12, scale=4),
            nullable=True,
            comment="Measurement temperature in Kelvin (spec §3.1 temp_K).",
        ),
    )
    op.add_column(
        "property_measurements",
        sa.Column(
            "pressure_gpa",
            sa.Numeric(precision=14, scale=6),
            nullable=True,
            comment="Measurement pressure in GPa (spec §3.1 pressure_GPa).",
        ),
    )

    # Containment index so `conditions @> '{"phase": "alpha"}'` stays cheap.
    op.create_index(
        "idx_pm_conditions_gin",
        "property_measurements",
        ["conditions"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_pm_simulation_method",
        "property_measurements",
        ["simulation_method"],
        unique=False,
        postgresql_where=sa.text("simulation_method IS NOT NULL"),
    )

    # Lossless backfill of the legacy 1-to-many condition rows. See (b):
    # the raw values are preserved verbatim under a 'legacy_conditions'
    # array; no unit is invented, and measurement_conditions is left intact.
    op.execute(
        sa.text(
            """
            UPDATE property_measurements pm
            SET conditions = jsonb_build_object('legacy_conditions', agg.rows)
            FROM (
                SELECT
                    mc.measurement_id,
                    jsonb_agg(
                        jsonb_strip_nulls(
                            jsonb_build_object(
                                'temperature', mc.temperature,
                                'pressure', mc.pressure,
                                'environment', mc.environment,
                                'irradiation_dose', mc.irradiation_dose,
                                'notes', mc.notes
                            )
                        )
                        ORDER BY mc.created_at, mc.id
                    ) AS rows
                FROM measurement_conditions mc
                GROUP BY mc.measurement_id
            ) AS agg
            WHERE pm.id = agg.measurement_id
            """
        )
    )


def _downgrade_conditions() -> None:
    op.drop_index("idx_pm_simulation_method", table_name="property_measurements")
    op.drop_index("idx_pm_conditions_gin", table_name="property_measurements")
    op.drop_column("property_measurements", "pressure_gpa")
    op.drop_column("property_measurements", "temp_k")
    op.drop_column("property_measurements", "model_name")
    op.drop_column("property_measurements", "simulation_method")
    op.drop_column("property_measurements", "conditions")


# ---------------------------------------------------------------------------
# 2. datasets literature identity — ADR-017 §2.5 ingest de-duplication
# ---------------------------------------------------------------------------


def _upgrade_dataset_identity() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "literature_doi",
            sa.String(length=255),
            nullable=True,
            comment="Normalised DOI — primary literature identity (ADR-017 §2.5).",
        ),
    )
    op.add_column(
        "datasets",
        sa.Column(
            "literature_content_hash",
            sa.String(length=64),
            nullable=True,
            comment="PDF content hash — identity fallback when DOI is absent.",
        ),
    )

    # Partial-unique: many legacy datasets have no DOI, and NULLs must not
    # collide with each other.
    op.create_index(
        "uq_datasets_literature_doi",
        "datasets",
        ["literature_doi"],
        unique=True,
        postgresql_where=sa.text("literature_doi IS NOT NULL"),
    )
    op.create_index(
        "idx_datasets_literature_content_hash",
        "datasets",
        ["literature_content_hash"],
        unique=False,
        postgresql_where=sa.text("literature_content_hash IS NOT NULL"),
    )


def _downgrade_dataset_identity() -> None:
    op.drop_index("idx_datasets_literature_content_hash", table_name="datasets")
    op.drop_index("uq_datasets_literature_doi", table_name="datasets")
    op.drop_column("datasets", "literature_content_hash")
    op.drop_column("datasets", "literature_doi")


# ---------------------------------------------------------------------------
# 3. dataset_versions — AC-6. ADR-017 §2.3 / spec §3.3
# ---------------------------------------------------------------------------


def _upgrade_dataset_versions() -> None:
    status_check = " OR ".join(f"status = '{value}'" for value in _DATASET_VERSION_STATUSES)

    op.create_table(
        "dataset_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_no",
            sa.Integer(),
            nullable=False,
            comment="Monotonic per dataset, starting at 1.",
        ),
        sa.Column(
            "row_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="property_measurements.id list captured by this snapshot.",
        ),
        sa.Column(
            "source_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="data_sources.id list contributing to this snapshot.",
        ),
        sa.Column(
            "parent_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
            comment="draft | released | rolled_back | superseded (ADR-017 §2.3).",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(status_check, name="ck_dataset_versions_status"),
        sa.CheckConstraint("version_no >= 1", name="ck_dataset_versions_version_no"),
        sa.UniqueConstraint(
            "dataset_id", "version_no", name="uq_dataset_versions_dataset_version_no"
        ),
    )

    op.create_index("idx_dataset_versions_dataset", "dataset_versions", ["dataset_id"])
    op.create_index("idx_dataset_versions_status", "dataset_versions", ["dataset_id", "status"])

    # ADR-017 §2.3 rollback is "切 released 指针" — a dataset therefore has at
    # most one released version at any instant. Enforced in the DB so a failed
    # pointer switch cannot leave two live versions serving the public API.
    op.create_index(
        "uq_dataset_versions_one_released",
        "dataset_versions",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'released'"),
    )


def _downgrade_dataset_versions() -> None:
    op.drop_index("uq_dataset_versions_one_released", table_name="dataset_versions")
    op.drop_index("idx_dataset_versions_status", table_name="dataset_versions")
    op.drop_index("idx_dataset_versions_dataset", table_name="dataset_versions")
    op.drop_table("dataset_versions")


# ---------------------------------------------------------------------------
# 4. dedupe_key — AC-9. ADR-017 §2.6
# ---------------------------------------------------------------------------


def _upgrade_dedupe_key() -> None:
    # Row -> owning snapshot. Also the discriminator for the partial unique
    # index below: NULL means "legacy, pre-versioning row" (decision (g)).
    op.add_column(
        "property_measurements",
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="SET NULL"),
            nullable=True,
            comment=(
                "Owning dataset_versions snapshot (spec §3.1). NULL marks a "
                "legacy row predating ADR-017 versioning; such rows are "
                "retained un-deduped per ADR-017 §4.1."
            ),
        ),
    )
    op.create_index(
        "idx_pm_dataset_version",
        "property_measurements",
        ["dataset_version_id"],
        unique=False,
        postgresql_where=sa.text("dataset_version_id IS NOT NULL"),
    )

    # Denormalised from datasets.source_id so the dedupe key is computable
    # from the row alone (a generated column cannot reach across a join).
    op.add_column(
        "property_measurements",
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
            nullable=True,
            comment="data_sources.id — denormalised from datasets.source_id.",
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE property_measurements pm
            SET source_id = d.source_id
            FROM datasets d
            WHERE pm.dataset_id = d.id
              AND d.source_id IS NOT NULL
            """
        )
    )
    op.create_index("idx_pm_source", "property_measurements", ["source_id"])

    # Generated, therefore drift-proof (decision (e)).
    op.execute(
        sa.text(
            "ALTER TABLE property_measurements "
            f"ADD COLUMN value_hash text GENERATED ALWAYS AS ({_VALUE_HASH_SQL.strip()}) STORED"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE property_measurements "
            f"ADD COLUMN dedupe_key text GENERATED ALWAYS AS ({_DEDUPE_KEY_SQL.strip()}) STORED"
        )
    )
    # ``ADD COLUMN ... GENERATED`` takes no COMMENT clause, so the comments
    # that keep Base.metadata aligned with the live schema are set here.
    op.execute(
        sa.text(
            "COMMENT ON COLUMN property_measurements.value_hash IS "
            "'GENERATED — md5 of the value columns + unit_id.'"
        )
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN property_measurements.dedupe_key IS "
            "'GENERATED — ADR-017 §2.6 composite dedupe key. Unique across "
            "rows carrying a dataset_version_id (uq_pm_dedupe_key).'"
        )
    )

    # AC-9. Partial by design — see decision (g).
    op.create_index(
        "uq_pm_dedupe_key",
        "property_measurements",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dataset_version_id IS NOT NULL"),
    )


def _downgrade_dedupe_key() -> None:
    op.drop_index("uq_pm_dedupe_key", table_name="property_measurements")
    op.drop_column("property_measurements", "dedupe_key")
    op.drop_column("property_measurements", "value_hash")
    op.drop_index("idx_pm_source", table_name="property_measurements")
    op.drop_column("property_measurements", "source_id")
    op.drop_index("idx_pm_dataset_version", table_name="property_measurements")
    op.drop_column("property_measurements", "dataset_version_id")
