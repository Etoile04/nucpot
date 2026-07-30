"""Add conditions_hash + method + composite UNIQUE constraint to measurements

Revision ID: 033_add_conditions_hash_and_method_to_measurements
Revises: 031_seed_property_types
Create Date: 2026-07-30

NFM-2032 (NFM-1972 AC-2): the cross-request dedup query in
``extraction_to_db_mapper.py`` must look up existing rows by the full
5-tuple dedup key:

    (dataset_id, property_type_id, conditions_hash, method)

The previous rejected schema (migration 032 on this branch) added only
``conditions_hash`` as a single-column index.  That produced three
defects detected in code review:

  * ``measurement_method`` was not persisted, so two scientifically
    distinct measurements (e.g. tensile vs nanoindentation on the same
    conditions) collapsed to a single row.
  * The lookup was not a UNIQUE constraint, so check-then-insert was
    always racy under concurrent requests.
  * Legacy rows had ``conditions_hash = NULL`` and therefore could not
    be matched on subsequent ingest.

This migration closes all three gaps:

  1. Adds ``method VARCHAR(100) NOT NULL DEFAULT ''`` and a
     ``conditions_hash`` NOT NULL backfill so the 5-tuple is fully
     populated for every row.
  2. Deduplicates the existing ``property_measurements`` table by
     (dataset_id, property_type_id, conditions_hash, method), keeping
     the oldest row and reassigning any ``MeasurementCondition`` /
     measurement-condition children to the kept row before deleting
     duplicates.
  3. Deduplicates the existing ``datasets`` table by
     (source_id, material_id), keeping the oldest dataset and
     reassigning every ``property_measurements.dataset_id`` to it
     before deleting the duplicates.
  4. Replaces the non-unique single-column index with the composite
     UNIQUE INDEX ``uq_pm_dedup`` and adds ``uq_datasets_source_material``
     so the DB enforces the 5-tuple invariant (and the dataset invariant).

The migration is reversible (down drops the new column, the new unique
indexes, and restores ``conditions_hash`` to nullable).

Note: down_revision = '031_seed_property_types' (main head).  Originally
chained on '032_create_data_submission_tables' from the NFM-2018 epic
branch, but that migration is not yet merged to main.  When NFM-2018
lands, its migration must re-point its down_revision to this revision.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "033_add_conditions_hash_and_method_to_measurements"
down_revision: str | Sequence[str] | None = "031_seed_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers (kept here so the migration is self-contained and reproducible)
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
    """SHA1-hash each historical row's MeasurementCondition into conditions_hash.

    Rows that have no MeasurementCondition rows hash to the empty-dict
    digest (the same one the mapper emits for ``conditions=None``), so
    legacy NULL/empty rows are reachable from the new dedup query.
    """
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
    """Collapse duplicate (dataset_id, property_type_id, conditions_hash, method) groups.

    Process per group:
      * Identify the kept row (oldest by ``created_at``, tie-broken by ``id``).
      * Reassign every ``MeasurementCondition.measurement_id`` that points
        at a to-be-deleted duplicate to the kept row.
      * Delete the duplicates.
    """
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
    """Collapse duplicate (source_id, material_id) Dataset groups.

    Process per group: keep the oldest dataset, reassign every
    ``property_measurements.dataset_id`` to the kept row, delete the
    duplicates.  MeasurementCondition rows are not affected because they
    hang off ``property_measurements``, not the dataset.
    """
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

    # 4. Backfill conditions_hash from MeasurementCondition rows.  Legacy
    #    rows may already have a non-NULL hash (from the rejected 032
    #    migration); the deterministic computation is idempotent so we
    #    overwrite unconditionally.
    _backfill_conditions_hash()

    # 5. Any rows that still have conditions_hash IS NULL (e.g. nested
    #    schema with no joined MeasurementCondition and no prior 032
    #    migration) get the empty-dict hash, then the column is set
    #    NOT NULL.
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

    # 6. Deduplicate existing rows so the upcoming UNIQUE indexes can be
    #    created without conflict on legacy duplicates.
    _dedupe_property_measurements()
    _dedupe_datasets()

    # 7. Drop the non-unique single-column index from the rejected 032
    #    migration (if it exists — wrapped in try/except because the
    #    column was never deployed to production).
    try:
        op.drop_index("idx_pm_conditions_hash", table_name="property_measurements")
    except Exception:  # pragma: no cover — index may not exist on fresh DBs
        pass

    # 8. Create the composite UNIQUE INDEX that makes the 5-tuple a DB
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
    """Reverse the migration: drop the unique indexes, the method column, and
    restore ``conditions_hash`` to nullable."""
    op.drop_index(
        "uq_datasets_source_material", table_name="datasets"
    )
    op.drop_index("uq_pm_dedup", table_name="property_measurements")
    # Restore the rejected-032 single-column index (idempotent w.r.t.
    # production, which never had it).
    op.create_index(
        "idx_pm_conditions_hash",
        "property_measurements",
        ["conditions_hash"],
    )
    op.alter_column(
        "property_measurements",
        "conditions_hash",
        existing_type=sa.String(40),
        nullable=True,
    )
    op.drop_column("property_measurements", "method")
