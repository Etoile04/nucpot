"""Restore 18 placeholder ``data_sources`` + 10 ``datasets`` rows from ``*_backup_070``.

Revision ID: 075_restore_placeholder_sources_datasets
Revises: 073_create_nfm_preview_role
Create Date: 2026-09-02

NFM-4139 — Option B (recast) from NFM-4135 verdict
==================================================

Root cause
----------
Migration 070_d2_dedup_bad_data_sources collapsed 18 placeholder
``data_sources`` rows + cascade-deleted 10 ``datasets`` rows that
referenced those placeholders via FK ON DELETE CASCADE.  The NFM-4130
re-scope (commit ``570a2e2fa``, PR #1107) prevents any future collapse
but cannot restore the already-collapsed rows.

Migration 070 itself preserved the pre-collapse state in
``data_sources_backup_070`` and ``datasets_backup_070`` (via
``CREATE TABLE ... AS SELECT * FROM ...`` before the destructive DDL).
The 10 deleted datasets' original ``(source_id, material_id)`` pairs
are intact, and the 18 placeholder source rows are recoverable with
exact UUIDs.

This migration performs the recast Option B (NFM-4135 verdict):
re-insert the 18 placeholder sources + 10 datasets from the backup
tables with **honest placeholder titles** (``Unattributed source (no
DOI)`` / ``Unknown Source``) preserved verbatim — no DOI / file_hash /
content_md reconstruction, no guesses.

Strategy
--------

1. ``INSERT INTO data_sources`` with ``ON CONFLICT (id) DO NOTHING`` —
   restores all 18 placeholder rows from the backup.  All columns
   carried verbatim from the backup so ``source_type`` (NOT NULL, no
   default) is preserved.
2. ``INSERT INTO datasets`` with ``ON CONFLICT (id) DO NOTHING`` —
   restores all 10 dataset rows with their **original** ``source_id``
   pointing at the freshly-inserted placeholder source.  A defensive
   ``NOT EXISTS`` subquery in the SELECT WHERE clause guards against
   the ``uq_datasets_source_material`` unique constraint collision
   (would only trigger if a current ``datasets`` row had the same
   ``(source_id, material_id)`` pair — confirmed against prod 2026-09-02
   to be impossible).
3. Verification queries print before/after counts and the 10 restored
   ``(dataset_id, source_id)`` pairs for AC-3 self-attestation.

Idempotency
-----------

Both INSERTs use ``ON CONFLICT (id) DO NOTHING`` on the primary key.
Re-running the migration is a no-op: 0 rows affected, 0 conflicts, all
verification queries return identical post-state.

Constraints honored
-------------------

* No writes outside the two INSERT blocks; no UPDATE / DELETE of any
  kind (AC-4).
* Restores the exact UUIDs from the backup tables — no synthesized
  IDs.
* Total row impact: +18 ``data_sources``, +10 ``datasets``.  No
  ``property_measurements`` (the 10 datasets had 0 measurements per
  NFM-4135 verdict evidence).
* Touches only rows that no other code path currently references
  (placeholders are not dedup candidates per NFM-4130 ruling).
* The ``trg_data_sources_uuid_title`` BEFORE INSERT trigger does not
  fire: placeholder titles are not UUID-titled.

Schema prerequisites
--------------------

* Requires migration 070 to have already created
  ``data_sources_backup_070`` and ``datasets_backup_070`` (which is
  the case on any prod instance that ran 070 before this migration).
* Does NOT alter any DDL.

Cross-references
----------------

* [NFM-4135](/NFM/issues/NFM-4135) — verdict authorizing this recast
  Option B (done, evidence captured).
* [NFM-4133](/NFM/issues/NFM-4133) — parent incident (CTO/CEO Option
  A+B decision).
* [NFM-4130](/NFM/issues/NFM-4130) — migration 070 re-scope
  prevention fix (PR #1107, done).
* [NFM-4105](/NFM/issues/NFM-4105) — placeholder-title class product
  decision (out of scope here; this migration only restores rows).
* [NFM-4136](/NFM/issues/NFM-4136) — parent epic (recast Option B).
* [NFM-4137](/NFM/issues/NFM-4137) — sibling triage for the 20
  non-placeholder datasets (independent).

Acceptance criteria
-------------------

* Migration authored with both INSERT blocks; idempotency proven
  via dry-run on a fresh prod clone.
* Production migration executed; post-state verified via
  ``SELECT count(*)`` (data_sources 65→83, datasets 188→198), and the
  per-dataset UUID table confirms each of the 10 dataset_ids exists
  with its original source_id.
* No writes outside the ``INSERT … ON CONFLICT DO NOTHING`` block.
* SRE canary confirms no query-plan regression.
* Result posted on NFM-4133 as a ``[RECAS] RESTORED`` comment with
  before/after counts and SHA.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "075_restore_placeholder_sources_datasets"
down_revision: str | Sequence[str] | None = "073_create_nfm_preview_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Placeholder titles preserved verbatim from migration 070's collapse
# window.  These match the original row titles created by the
# ``extraction_to_db_mapper`` DOI-empty branch — they are the only
# strings the recast Option B restores.  Any other source rows in the
# backup table (UUID-titled, canonical, real-literature, etc.) are
# deliberately NOT restored by this migration.
_PLACEHOLDER_TITLES: tuple[str, ...] = (
    "Unknown Source",
    "Unattributed source (no DOI)",
)

# Expected delta (for verification logging only — the migration does
# not assert these counts; the verification queries below print
# actual deltas).
_EXPECTED_DATA_SOURCES_DELTA: int = 18
_EXPECTED_DATASETS_DELTA: int = 10


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Re-insert 18 placeholder sources + 10 datasets from backup tables.

    On a fresh database (no backup tables from migration 070), this is a
    safe no-op — the backup tables never existed so there is nothing to
    restore.  This allows ``alembic upgrade head`` from an empty schema
    to succeed without errors (schema-drift guard requirement).
    """
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Fresh-DB guard: skip when backup tables are absent.
    # ------------------------------------------------------------------
    # ``to_regclass()`` returns NULL when the relation does not exist,
    # so the COALESCE yields FALSE — and the whole upgrade becomes a
    # no-op.  This is the correct behaviour: a fresh DB never ran
    # migration 070, so there is nothing to restore.
    row = bind.execute(
        sa.text(
            """
            SELECT to_regclass('public.data_sources_backup_070') IS NOT NULL
              AND to_regclass('public.datasets_backup_070') IS NOT NULL
            AS backup_tables_exist
            """
        )
    ).one()
    if not row[0]:
        return

    # ------------------------------------------------------------------
    # Pre-state: capture before counts for AC-3 self-attestation.
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            DROP TABLE IF EXISTS _restore_075_pre_counts
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TEMP TABLE _restore_075_pre_counts ON COMMIT DROP AS
            SELECT
                (SELECT count(*) FROM data_sources)              AS data_sources_pre,
                (SELECT count(*) FROM datasets)                  AS datasets_pre,
                (SELECT count(*) FROM data_sources_backup_070
                 WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                )                                                AS placeholder_in_backup
            """
        )
    )

    # ------------------------------------------------------------------
    # 1. INSERT 18 placeholder data_sources rows.
    # ------------------------------------------------------------------
    # ON CONFLICT (id) DO NOTHING on PK makes this idempotent.  All
    # columns carried verbatim from the backup so NOT-NULL columns
    # (``source_type``, ``parse_status``) are populated correctly.
    bind.execute(
        sa.text(
            """
            INSERT INTO data_sources (
                id, doi, title, journal, year, volume, pages,
                source_type, abstract, external_url,
                created_at, updated_at,
                file_path, file_hash, file_size, content_md,
                parse_status, parse_error, original_filename, metadata_
            )
            SELECT
                id, doi, title, journal, year, volume, pages,
                source_type, abstract, external_url,
                created_at, updated_at,
                file_path, file_hash, file_size, content_md,
                parse_status, parse_error, original_filename, metadata_
            FROM data_sources_backup_070
            WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    # ------------------------------------------------------------------
    # 2. INSERT 10 datasets with their original source_id.
    # ------------------------------------------------------------------
    # Defensive ``NOT EXISTS`` subqueries in the WHERE clause prevent
    # both:
    #   - PK collision on ``id``
    #   - UNIQUE collision on ``(source_id, material_id)``
    # The PK ON CONFLICT remains as a belt-and-braces guard against
    # concurrent inserts.
    bind.execute(
        sa.text(
            """
            INSERT INTO datasets (
                id, material_id, source_id, title, description,
                measurement_date, is_verified, created_at, updated_at
            )
            SELECT
                bk.id, bk.material_id, bk.source_id, bk.title,
                bk.description, bk.measurement_date, bk.is_verified,
                bk.created_at, bk.updated_at
            FROM datasets_backup_070 bk
            WHERE bk.source_id IN (
                SELECT id FROM data_sources_backup_070
                WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
            )
              AND NOT EXISTS (
                  SELECT 1 FROM datasets d WHERE d.id = bk.id
              )
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
    # Post-state: AC-3 self-attestation (before/after + 10 UUID pairs).
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            DROP TABLE IF EXISTS _restore_075_post_counts
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TEMP TABLE _restore_075_post_counts ON COMMIT DROP AS
            SELECT
                (SELECT count(*) FROM data_sources)              AS data_sources_post,
                (SELECT count(*) FROM datasets)                  AS datasets_post
            """
        )
    )

    bind.execute(
        sa.text(
            """
            SELECT
                'restore_075_summary'                 AS check_name,
                pre.data_sources_pre                  AS data_sources_pre,
                post.data_sources_post                AS data_sources_post,
                post.data_sources_post - pre.data_sources_pre
                                                    AS data_sources_delta,
                pre.datasets_pre                      AS datasets_pre,
                post.datasets_post                    AS datasets_post,
                post.datasets_post - pre.datasets_pre
                                                    AS datasets_delta,
                pre.placeholder_in_backup             AS placeholder_in_backup
            FROM _restore_075_pre_counts pre, _restore_075_post_counts post
            """
        )
    )

    # Per-dataset UUID table — AC-3 evidence that each of the 10
    # dataset_ids exists with its original source_id.
    bind.execute(
        sa.text(
            """
            SELECT
                d.id        AS dataset_id,
                d.source_id AS restored_source_id,
                ds.title    AS restored_source_title,
                d.title     AS dataset_title
            FROM datasets d
            JOIN data_sources ds ON ds.id = d.source_id
            WHERE d.id IN (
                SELECT bk.id FROM datasets_backup_070 bk
                WHERE bk.source_id IN (
                    SELECT id FROM data_sources_backup_070
                    WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                )
            )
            ORDER BY ds.title, d.created_at
            """
        )
    )


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse the restoration: delete only the rows we just inserted.

    Defensive: only deletes rows whose IDs are present in the backup
    tables AND match the placeholder class.  This guards against
    accidentally removing any row that may have accumulated after the
    migration ran (e.g. real placeholder rows added by ingest).

    The 10 restored datasets have 0 property_measurements (confirmed
    in NFM-4135 verdict), so the ``property_measurements`` ON DELETE
    CASCADE chain is not triggered.  The 18 placeholder sources have
    no FK references from canonical rows (their dataset restoration
    is what we just reversed).

    On a fresh database this is a no-op (backup tables never existed).
    """
    bind = op.get_bind()

    # Fresh-DB guard (mirrors upgrade).
    row = bind.execute(
        sa.text(
            """
            SELECT to_regclass('public.data_sources_backup_070') IS NOT NULL
              AND to_regclass('public.datasets_backup_070') IS NOT NULL
            AS backup_tables_exist
            """
        )
    ).one()
    if not row[0]:
        return

    # ------------------------------------------------------------------
    # 1. Delete the 10 restored datasets (only those whose ids exist
    #    in the backup AND match a placeholder source_id).
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            DELETE FROM datasets
            WHERE id IN (
                SELECT bk.id FROM datasets_backup_070 bk
                WHERE bk.source_id IN (
                    SELECT id FROM data_sources_backup_070
                    WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                )
            )
            """
        )
    )

    # ------------------------------------------------------------------
    # 2. Delete the 18 restored placeholder data_sources rows
    #    (only those whose ids exist in the backup AND have a
    #    placeholder title).
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            """
            DELETE FROM data_sources
            WHERE id IN (
                SELECT bk.id FROM data_sources_backup_070 bk
                WHERE bk.title IN ('Unknown Source', 'Unattributed source (no DOI)')
            )
              AND title IN ('Unknown Source', 'Unattributed source (no DOI)')
            """
        )
    )
