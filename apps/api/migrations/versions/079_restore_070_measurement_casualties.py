"""Restore the 31 ``property_measurements`` cascade-deleted by migration 070.

Revision ID: 079_restore_070_measurement_casualties
Revises: 078_data_origin_state
Create Date: 2026-09-02

NFM-4191 — [SRE-CRITICAL] prod DB migration lag / 070 data loss
===============================================================

Root cause (prod ground truth, audited 2026-09-02)
--------------------------------------------------

Migration ``070_d2_dedup_bad_data_sources`` deleted **30** ``datasets``
rows whose sources it collapsed (20 on UUID-titled sources, 10 on
placeholder-titled sources).  ``property_measurements.dataset_id`` is
``ON DELETE CASCADE``, so those datasets took **31 measurement rows**
with them — including every real-material measurement in the database
(UO2 7, U-10Mo 3, UO/ZrNb/ZrNb-1/Cr2O3/C23/C33/CuAu/C55 1 each, plus 13
on Unknown Material).  User-visible symptom: the UO2 detail page showed
0 property rows where it used to show 7.

Migration 075 (NFM-4139) restored the 18 placeholder ``data_sources`` +
10 placeholder ``datasets`` but deliberately no measurements: the
NFM-4135 verdict recorded those datasets as having "0 measurements" —
which was true *post-cascade* but not pre-cascade.  The verdict mistook
the after-state for the before-state; this migration closes that gap.

Deterministic casualty class (state-independent)
------------------------------------------------

The restore scope is defined so both ``upgrade`` and ``downgrade``
compute the identical class from the ``*_backup_070`` snapshot alone:

* **class datasets** — backup datasets that (a) carried at least one
  backup measurement and (b) whose backup source is collapsed-class
  (UUID-titled or placeholder-titled): exactly **30** rows.
* **class sources** — distinct ``source_id`` of those datasets: **24**
  rows (6 UUID-titled + 18 placeholder-titled).
* **class measurements** — backup measurements on class datasets:
  exactly **31** rows.

Verified against prod 2026-09-02 (post-078): the 31 are precisely the
backup rows missing from ``property_measurements`` (83 of 114 remain),
no surviving measurement sits on a class dataset, and the restored
per-material distribution equals the backup baseline exactly.

Trigger interaction (NFM-4097 / migration 071)
----------------------------------------------

6 of the class sources are UUID-titled; ``trg_data_sources_uuid_title``
(``reject_uuid_titled_source``) hard-rejects such INSERTs.  This
migration disables that trigger around the source INSERT and re-enables
it immediately after.  ``ALTER TABLE … DISABLE TRIGGER`` is transactional
DDL: a failure anywhere in the migration rolls the disable back with the
rest of the transaction, so other sessions can never observe a disabled
guard.  On databases without the trigger (e.g. 071 downgraded) the step
is skipped.

NFM-4171 / asyncpg safety
-------------------------

No temp tables, no bind parameters, no multi-statement round-trips:
counts are captured into Python scalars via single ``SELECT count(*)``
statements and re-emitted as integer literals in the AC summary, the
same pattern migration 075 adopted after the UndefinedTable incident.

Idempotency
-----------

Every INSERT uses ``NOT EXISTS`` guards plus ``ON CONFLICT DO NOTHING``
(plain, i.e. any constraint) so re-running is a no-op and the
``uq_pm_dedup (dataset_id, property_type_id, conditions_hash, method)``
constraint can never abort the restore: winners of the 070 fold live on
non-class datasets, so restored rows cannot collide with them.

Fresh-DB guard
--------------

On a database that never ran migration 070 the backup tables do not
exist and this migration is a no-op (schema-drift guard requirement).

Rollback
--------

``downgrade()`` deletes the class in FK-safe order (measurements →
datasets → unreferenced sources).  For total incidents the primary
rollback is the pre-restore pg_dump (see NFM-4191 run book); the
downgrade is the surgical path.

Cross-references
----------------

* [NFM-4191](/NFM/issues/NFM-4191) — this restore (SRE-CRITICAL).
* [NFM-4139](/NFM/issues/NFM-4139) / [NFM-4135](/NFM/issues/NFM-4135) —
  placeholder-class restore and the verdict whose "0 measurements"
  premise this migration corrects.
* [NFM-4137](/NFM/issues/NFM-4137) — sibling triage for the 20
  non-placeholder datasets (rows restored here as data carriers).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "079_restore_070_measurement_casualties"
down_revision: str | Sequence[str] | None = "078_data_origin_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Collapsed-source class from migration 070's own scoping: placeholder
# titles (extraction_to_db_mapper DOI-empty branch) and UUID titles
# (the class migration 071 later guarded against).
_PLACEHOLDER_TITLES: tuple[str, ...] = (
    "Unknown Source",
    "Unattributed source (no DOI)",
)

_UUID_TITLE_REGEX: str = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# Shared class predicate: backup datasets that carried measurements and
# whose backup source is collapsed-class.  Embedded verbatim into every
# INSERT/DELETE below so upgrade and downgrade cannot drift apart.
_CLASS_DATASETS_SQL: str = f"""
SELECT bk.id
FROM datasets_backup_070 bk
JOIN data_sources_backup_070 s ON s.id = bk.source_id
WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                FROM property_measurements_backup_070 pm)
  AND (s.title ~ '{_UUID_TITLE_REGEX}'
       OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
"""

_CLASS_SOURCES_SQL: str = f"""
SELECT DISTINCT bk.source_id
FROM datasets_backup_070 bk
JOIN data_sources_backup_070 s ON s.id = bk.source_id
WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                FROM property_measurements_backup_070 pm)
  AND (s.title ~ '{_UUID_TITLE_REGEX}'
       OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
"""


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Re-insert class sources, datasets and measurements from backup."""
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Fresh-DB guard: skip when any 070 backup table is absent.
    # ------------------------------------------------------------------
    row = bind.execute(
        sa.text(
            """
            SELECT to_regclass('public.property_measurements_backup_070') IS NOT NULL
              AND to_regclass('public.datasets_backup_070') IS NOT NULL
              AND to_regclass('public.data_sources_backup_070') IS NOT NULL
            AS backup_tables_exist
            """
        )
    ).one()
    if not row[0]:
        return

    # ------------------------------------------------------------------
    # Pre-state (Python scalars — NFM-4171 asyncpg-safe pattern).
    # ------------------------------------------------------------------
    pre_row = bind.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*) FROM data_sources)         AS sources_pre,
                (SELECT count(*) FROM datasets)             AS datasets_pre,
                (SELECT count(*) FROM property_measurements) AS meas_pre
            """
        )
    ).one()
    sources_pre, datasets_pre, meas_pre = (
        int(pre_row[0]),
        int(pre_row[1]),
        int(pre_row[2]),
    )

    # ------------------------------------------------------------------
    # 1. INSERT class sources (UUID-titled + placeholder-titled).
    # ------------------------------------------------------------------
    # The uuid-title guard trigger (071) must not veto the restore of
    # historical rows: disable it inside this transaction, re-enable
    # right after.  Transactional DDL means a later failure rolls the
    # DISABLE back with everything else.
    trigger_row = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_data_sources_uuid_title'
                  AND tgrelid = 'public.data_sources'::regclass
                  AND NOT tgisinternal
            ) AS guard_exists
            """
        )
    ).one()
    guard_exists = bool(trigger_row[0])

    if guard_exists:
        bind.execute(
            sa.text("ALTER TABLE public.data_sources DISABLE TRIGGER trg_data_sources_uuid_title")
        )

    bind.execute(
        sa.text(
            f"""
            INSERT INTO data_sources (
                id, doi, title, journal, year, volume, pages,
                source_type, abstract, external_url,
                created_at, updated_at,
                file_path, file_hash, file_size, content_md,
                parse_status, parse_error, original_filename, metadata_
            )
            SELECT
                b.id, b.doi, b.title, b.journal, b.year, b.volume, b.pages,
                b.source_type, b.abstract, b.external_url,
                b.created_at, b.updated_at,
                b.file_path, b.file_hash, b.file_size, b.content_md,
                b.parse_status, b.parse_error, b.original_filename, b.metadata_
            FROM data_sources_backup_070 b
            WHERE b.id IN ({_CLASS_SOURCES_SQL})
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    if guard_exists:
        bind.execute(
            sa.text("ALTER TABLE public.data_sources ENABLE TRIGGER trg_data_sources_uuid_title")
        )

    # ------------------------------------------------------------------
    # 2. INSERT class datasets with their original source_id.
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            f"""
            INSERT INTO datasets (
                id, material_id, source_id, title, description,
                measurement_date, is_verified, created_at, updated_at
            )
            SELECT
                bk.id, bk.material_id, bk.source_id, bk.title,
                bk.description, bk.measurement_date, bk.is_verified,
                bk.created_at, bk.updated_at
            FROM datasets_backup_070 bk
            WHERE bk.id IN ({_CLASS_DATASETS_SQL})
              AND NOT EXISTS (SELECT 1 FROM datasets d WHERE d.id = bk.id)
              AND NOT EXISTS (
                  SELECT 1 FROM datasets d
                  WHERE d.source_id = bk.source_id
                    AND d.material_id = bk.material_id
              )
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    # ------------------------------------------------------------------
    # 3. INSERT class measurements, columns carried verbatim.
    # ------------------------------------------------------------------
    # Plain ON CONFLICT DO NOTHING (no target) also covers uq_pm_dedup
    # as a belt-and-braces guard; fold winners live on non-class
    # datasets so no collision is expected on prod data.
    bind.execute(
        sa.text(
            f"""
            INSERT INTO property_measurements (
                id, dataset_id, property_type_id,
                value_scalar, value_min, value_max,
                value_expression, value_list, value_text,
                uncertainty, unit_id, notes,
                created_at, updated_at,
                review_status, reviewer_note, reviewed_at,
                method, conditions_hash
            )
            SELECT
                b.id, b.dataset_id, b.property_type_id,
                b.value_scalar, b.value_min, b.value_max,
                b.value_expression, b.value_list, b.value_text,
                b.uncertainty, b.unit_id, b.notes,
                b.created_at, b.updated_at,
                b.review_status, b.reviewer_note, b.reviewed_at,
                b.method, b.conditions_hash
            FROM property_measurements_backup_070 b
            WHERE b.dataset_id IN ({_CLASS_DATASETS_SQL})
              AND NOT EXISTS (
                  SELECT 1 FROM property_measurements pm WHERE pm.id = b.id
              )
            ON CONFLICT DO NOTHING
            """
        )
    )

    # ------------------------------------------------------------------
    # Post-state + AC self-attestation (integer literals, no binds).
    # ------------------------------------------------------------------
    post_row = bind.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*) FROM data_sources)          AS sources_post,
                (SELECT count(*) FROM datasets)              AS datasets_post,
                (SELECT count(*) FROM property_measurements) AS meas_post
            """
        )
    ).one()
    sources_post, datasets_post, meas_post = (
        int(post_row[0]),
        int(post_row[1]),
        int(post_row[2]),
    )

    bind.execute(
        sa.text(
            f"""
            SELECT
                'restore_079_summary'                          AS check_name,
                {sources_pre}                                  AS sources_pre,
                {sources_post}                                 AS sources_post,
                {sources_post - sources_pre}                   AS sources_delta,
                {datasets_pre}                                 AS datasets_pre,
                {datasets_post}                                AS datasets_post,
                {datasets_post - datasets_pre}                 AS datasets_delta,
                {meas_pre}                                     AS meas_pre,
                {meas_post}                                    AS meas_post,
                {meas_post - meas_pre}                         AS meas_delta
            """
        )
    )


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse the restore in FK-safe order.

    Deletes only rows in the deterministic class: measurements on class
    datasets (ON DELETE CASCADE would also cover them via step 2, but
    deleting explicitly keeps the intent auditable), then the class
    datasets, then class sources that no dataset references any more
    (placeholder sources commonly serve surviving datasets — e.g. the
    Unknown Material canonical cluster — and must not be dropped).
    """
    bind = op.get_bind()

    row = bind.execute(
        sa.text(
            """
            SELECT to_regclass('public.property_measurements_backup_070') IS NOT NULL
              AND to_regclass('public.datasets_backup_070') IS NOT NULL
              AND to_regclass('public.data_sources_backup_070') IS NOT NULL
            AS backup_tables_exist
            """
        )
    ).one()
    if not row[0]:
        return

    bind.execute(
        sa.text(
            f"""
            DELETE FROM property_measurements
            WHERE dataset_id IN ({_CLASS_DATASETS_SQL})
            """
        )
    )

    bind.execute(
        sa.text(
            f"""
            DELETE FROM datasets
            WHERE id IN ({_CLASS_DATASETS_SQL})
            """
        )
    )

    bind.execute(
        sa.text(
            f"""
            DELETE FROM data_sources ds
            WHERE ds.id IN ({_CLASS_SOURCES_SQL})
              AND NOT EXISTS (
                  SELECT 1 FROM datasets d WHERE d.source_id = ds.id
              )
            """
        )
    )
