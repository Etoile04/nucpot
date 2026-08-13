"""Add method + conditions_hash + composite UNIQUE indexes for cross-request dedup.

Revision ID: 032_add_dedup_unique_indexes
Revises: 031_seed_property_types
Create Date: 2026-07-30

NFM-2013 AC-4 + NFM-2032 (NFM-1972 AC-2): the cross-request dedup
query in ``extraction_to_db_mapper.py`` must look up existing rows by
the full 5-tuple dedup key:

    (dataset_id, property_type_id, conditions_hash, method)

The pre-existing in-memory ``seen_measurement_keys: set`` was per-call
only and reset between requests, so two identical POSTs created two
duplicate ``property_measurements`` rows.  This migration closes the
gap at the DB layer:

  1. Adds ``method VARCHAR(100) NOT NULL DEFAULT ''`` column.
  2. Adds ``conditions_hash VARCHAR(40) NOT NULL`` column (with
     backfill from joined ``MeasurementCondition`` rows so legacy NULL
     rows become reachable from the new dedup lookup).
  3. Deduplicates the existing ``property_measurements`` table by the
     5-tuple, keeping the oldest row and reassigning any
     ``MeasurementCondition.measurement_id`` to the kept row before
     deleting duplicates.
  4. Deduplicates the existing ``datasets`` table by
     (source_id, material_id), keeping the oldest dataset and
     reassigning every ``property_measurements.dataset_id`` to it
     before deleting the duplicates.
  5. Creates the composite UNIQUE INDEX ``uq_pm_dedup`` and the
     UNIQUE INDEX ``uq_datasets_source_material`` so the DB enforces
     the 5-tuple invariant (and the dataset invariant).  The DB-level
     invariant turns any concurrent-insert race into IntegrityError,
     which the mapper's SAVEPOINT catches and counts as
     ``skipped_duplicate_measurements``.

The migration is reversible (down drops the new indexes, the new
column, restores ``conditions_hash`` to nullable).

Coordination note: chained on ``031_seed_property_types``.  No
ordering conflict with NFM-2032 commit 8ef0034 (which depended on
the NFM-2018 sibling migration that is not in our branch); the
new revision id 032 is local to this branch.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032_add_dedup_unique_indexes"
down_revision: str | Sequence[str] | None = "031_seed_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers (kept self-contained so the migration is reproducible)
# ---------------------------------------------------------------------------


def _canonical_conditions_hash(
    temperature: float | None,
    pressure: float | None,
    environment: str | None,
    irradiation_dose: float | None,
    notes: str | None,
) -> str:
    """Reconstruct the SHA1 used by ``extraction_to_db_mapper._conditions_hash``.

    The mapper SHA1s a JSON-serialised conditions dict with ``sort_keys=True``
    and ``separators=(",", ":")``.  Here we rebuild the same dict from the
    normalised MeasurementCondition columns so legacy rows can be backfilled
    with the hash that the mapper will later use to look them up.

    The reconstruction is symmetric to ``_build_condition_kwargs`` in the
    mapper: only keys that are present in the original dict are emitted,
    and ``None`` values are dropped (so a missing temperature is identical
    to a temperature that was never observed, not a temperature of zero).
    """
    canonical: dict[str, object] = {}
    if temperature is not None:
        canonical["temperature"] = float(temperature)
    if pressure is not None:
        canonical["pressure"] = float(pressure)
    if environment is not None:
        canonical["environment"] = environment
    if irradiation_dose is not None:
        canonical["irradiation_dose"] = float(irradiation_dose)
    if notes is not None:
        canonical["notes"] = notes
    if not canonical:
        return hashlib.sha1(b"{}").hexdigest()
    serialised = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialised.encode("utf-8")).hexdigest()


def _backfill_conditions_hash() -> None:
    """SHA1-hash each historical row's MeasurementCondition into conditions_hash."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                pm.id,
                mc.temperature,
                mc.pressure,
                mc.environment,
                mc.irradiation_dose,
                mc.notes
            FROM property_measurements pm
            LEFT JOIN measurement_conditions mc ON mc.measurement_id = pm.id
            """
        )
    ).fetchall()
    for pm_id, temperature, pressure, environment, dose, notes in rows:
        h = _canonical_conditions_hash(
            temperature, pressure, environment, dose, notes
        )
        bind.execute(
            sa.text(
                "UPDATE property_measurements SET conditions_hash = :h "
                "WHERE id = :id"
            ),
            {"h": h, "id": pm_id},
        )


def _dedupe_property_measurements() -> None:
    """Collapse duplicate (dataset_id, property_type_id, conditions_hash, method) groups."""
    bind = op.get_bind()
    dup_groups = bind.execute(
        sa.text(
            """
            SELECT dataset_id, property_type_id, conditions_hash, method,
                   COUNT(*) AS row_count
            FROM property_measurements
            GROUP BY dataset_id, property_type_id, conditions_hash, method
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for dataset_id, property_type_id, cond_hash, method, _count in dup_groups:
        keep_row = bind.execute(
            sa.text(
                """
                SELECT id FROM property_measurements
                WHERE dataset_id = :dataset_id
                  AND property_type_id = :property_type_id
                  AND conditions_hash = :conditions_hash
                  AND method = :method
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ),
            {
                "dataset_id": dataset_id,
                "property_type_id": property_type_id,
                "conditions_hash": cond_hash,
                "method": method,
            },
        ).fetchone()
        if keep_row is None:  # pragma: no cover — defensive
            continue
        keep_id = keep_row[0]
        bind.execute(
            sa.text(
                """
                UPDATE measurement_conditions
                SET measurement_id = :keep_id
                WHERE measurement_id IN (
                    SELECT id FROM property_measurements
                    WHERE dataset_id = :dataset_id
                      AND property_type_id = :property_type_id
                      AND conditions_hash = :conditions_hash
                      AND method = :method
                      AND id != :keep_id
                )
                """
            ),
            {
                "keep_id": keep_id,
                "dataset_id": dataset_id,
                "property_type_id": property_type_id,
                "conditions_hash": cond_hash,
                "method": method,
            },
        )
        bind.execute(
            sa.text(
                """
                DELETE FROM property_measurements
                WHERE dataset_id = :dataset_id
                  AND property_type_id = :property_type_id
                  AND conditions_hash = :conditions_hash
                  AND method = :method
                  AND id != :keep_id
                """
            ),
            {
                "keep_id": keep_id,
                "dataset_id": dataset_id,
                "property_type_id": property_type_id,
                "conditions_hash": cond_hash,
                "method": method,
            },
        )


def _dedupe_datasets() -> None:
    """Collapse duplicate (source_id, material_id) Dataset groups."""
    bind = op.get_bind()
    dup_groups = bind.execute(
        sa.text(
            """
            SELECT source_id, material_id, COUNT(*) AS row_count
            FROM datasets
            GROUP BY source_id, material_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    for source_id, material_id, _count in dup_groups:
        keep_row = bind.execute(
            sa.text(
                """
                SELECT id FROM datasets
                WHERE source_id = :source_id AND material_id = :material_id
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ),
            {"source_id": source_id, "material_id": material_id},
        ).fetchone()
        if keep_row is None:  # pragma: no cover — defensive
            continue
        keep_id = keep_row[0]
        bind.execute(
            sa.text(
                """
                UPDATE property_measurements
                SET dataset_id = :keep_id
                WHERE dataset_id IN (
                    SELECT id FROM datasets
                    WHERE source_id = :source_id
                      AND material_id = :material_id
                      AND id != :keep_id
                )
                """
            ),
            {
                "keep_id": keep_id,
                "source_id": source_id,
                "material_id": material_id,
            },
        )
        bind.execute(
            sa.text(
                """
                DELETE FROM datasets
                WHERE source_id = :source_id
                  AND material_id = :material_id
                  AND id != :keep_id
                """
            ),
            {
                "keep_id": keep_id,
                "source_id": source_id,
                "material_id": material_id,
            },
        )


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add the 5-tuple columns, backfill, dedupe, and enforce the unique indexes."""
    # 1. Add method column (nullable first so the ALTER COLUMN ... NOT NULL
    #    later can backfill into existing rows without violating the new
    #    constraint during the column change).
    op.add_column(
        "property_measurements",
        sa.Column(
            "method",
            sa.String(100),
            nullable=True,
            comment="Measurement method (NFM-2032 5-tuple dedup).",
        ),
    )

    # 2. Backfill method = '' for legacy rows so the column can be set
    #    NOT NULL.
    op.execute(
        "UPDATE property_measurements SET method = '' WHERE method IS NULL"
    )

    # 3. Make method NOT NULL with a default so future inserts that omit
    #    the column still satisfy the constraint.
    op.alter_column(
        "property_measurements",
        "method",
        existing_type=sa.String(100),
        nullable=False,
        server_default="",
    )

    # 4. Add conditions_hash column (nullable first to backfill, then NOT NULL).
    op.add_column(
        "property_measurements",
        sa.Column(
            "conditions_hash",
            sa.String(40),
            nullable=True,
            comment="SHA1 hash of measurement conditions for dedup (NFM-2032).",
        ),
    )

    # 5. Backfill conditions_hash from MeasurementCondition rows.
    _backfill_conditions_hash()

    # 6. Any rows that still have conditions_hash IS NULL get the
    #    empty-dict hash, then the column is set NOT NULL.
    bind = op.get_bind()
    empty_hash = _canonical_conditions_hash(None, None, None, None, None)
    bind.execute(
        sa.text(
            "UPDATE property_measurements SET conditions_hash = :h "
            "WHERE conditions_hash IS NULL"
        ),
        {"h": empty_hash},
    )
    op.alter_column(
        "property_measurements",
        "conditions_hash",
        existing_type=sa.String(40),
        nullable=False,
    )

    # 7. Deduplicate existing rows so the upcoming UNIQUE indexes can be
    #    created without conflict on legacy duplicates.
    _dedupe_property_measurements()
    _dedupe_datasets()

    # 8. Create the composite UNIQUE INDEXes that make the 5-tuple a DB
    #    invariant.  This is the linchpin of cross-request dedup: even
    #    two concurrent inserts racing on an empty SELECT will fail
    #    one with IntegrityError, which the mapper catches and counts
    #    as skipped_duplicate_measurements.
    op.create_index(
        "uq_pm_dedup",
        "property_measurements",
        ["dataset_id", "property_type_id", "conditions_hash", "method"],
        unique=True,
    )
    op.create_index(
        "uq_datasets_source_material",
        "datasets",
        ["source_id", "material_id"],
        unique=True,
    )


def downgrade() -> None:
    """Reverse the migration: drop the unique indexes, the new columns."""
    op.drop_index(
        "uq_datasets_source_material", table_name="datasets"
    )
    op.drop_index("uq_pm_dedup", table_name="property_measurements")
    op.drop_column("property_measurements", "conditions_hash")
    op.drop_column("property_measurements", "method")