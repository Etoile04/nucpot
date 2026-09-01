"""D2 dedup — collapse UUID-titled ``data_sources`` rows into canonical rows.

Revision ID: 070_d2_dedup_bad_data_sources
Revises: 069_add_v050_f8_property_types
Create Date: 2026-09-02

NFM-4088 / NFM-4092 / NFM-4099 / NFM-4104 — D2 data-side persistent fix
=====================================================================

Root cause
----------
``extraction_to_db_mapper.py:709-717`` (DOI-empty branch) inserts a new
``DataSource`` row with the item's ``reference`` (or placeholder) as
``title`` and no DOI.  When the upstream extraction chain supplied a
*previous* source's UUID instead of a real reference, the new row's
``title`` became a 36-char UUID pattern.  Repeating this for every
re-run created ~14 bad rows whose ``title`` matches the canonical UUID
regex; ~20 ``property_measurement`` rows reference these bad sources.

Strategy (NFM-4104 re-scope)
----------------------------
0. **Bad class = UUID-titled rows only.** Placeholder titles
   ``'Unknown Source'`` / ``'Unattributed source (no DOI)'`` are 58
   distinct real sources on staging (18 on prod) with no DOI,
   ``file_hash`` or ``content_md`` and are **not** dedup candidates —
   see [NFM-4092](/NFM/issues/NFM-4092) for evidence and the CTO ruling.
   Tracked separately as [NFM-4105](/NFM/issues/NFM-4105).
1. **Deterministic canonical resolution.** ``canonical_id := title::uuid``.
   All UUID-titled rows whose title parses to an existing
   ``data_sources.id`` that is itself non-bad are resolvable; rows that
   don't resolve are kept (skipped, not deleted) so attribution is
   never silently lost.
2. **One winner dataset per ``(canonical_source, material)``** — keeps
   the ``uq_datasets_source_material`` constraint intact.
3. **Single measurement move** mirroring
   ``uq_pm_dedup (dataset_id, property_type_id, conditions_hash,
   method)`` with NULLS DISTINCT semantics. Anything left on a loser
   dataset duplicates a row the canonical already owns; canonical wins.
4. **Guard stays ``RAISE EXCEPTION``** — do not downgrade (NFM-4092
   ruling). Scoped to the rows the migration actually deletes.
5. **No bind parameters** — regex is inlined as a SQL literal so
   asyncpg-against-``DO`` is satisfied (NFM-4099).

``downgrade()`` and the ``*_backup_070`` snapshot logic are unchanged.

Cross-references
----------------
* NFM-4084 — 决策表 + 根因
* NFM-4086 — D1 schema (independent of this fix's success; D2 migration
  is closed-form on the current schema)
* NFM-4087 — visual fast-fix (independent, not blocking)
* NFM-4088 — original D2 migration (now re-scoped)
* NFM-4091 — F4 ingest-path monitoring (independent follow-up)
* NFM-4092 — parent blocker ruling: keep guard loud, re-scope here
* NFM-4099 — asyncpg ``DO`` bind-param crash (literal-inlining fix)
* NFM-4104 — this re-scope (deterministic title::uuid, no sha256)
* NFM-4105 — placeholder-title class follow-up (separate ticket, NOT
  part of this migration)

Verified replacement body for ``upgrade()`` lives in
``docs/verification/NFM-4092-070-rescope-verified.sql`` (executed on
live staging data inside ``BEGIN … ROLLBACK``; guard passed with zero
data loss).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "070_d2_dedup_bad_data_sources"
down_revision: str | Sequence[str] | None = "069_add_v050_f8_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Forward dedup SQL — single plpgsql block, no bind parameters.
#
# Body verified against live staging data; see the docstring above and
# ``docs/verification/NFM-4092-070-rescope-verified.sql`` for the source
# of truth. This copy is canonical; if they diverge, the .sql is right.
#
# Key constraints honoured:
#   * No bind parameters (asyncpg refuses them on DO blocks — NFM-4099).
#   * Bad class = UUID-titled rows only; placeholder titles untouched
#     (NFM-4104 / NFM-4105).
#   * canonical_id := title::uuid; unresolvable rows are skipped, not
#     deleted, so attribution is never silently lost.
#   * One winner dataset per (canonical_source, material) — preserves
#     uq_datasets_source_material.
#   * Single measurement move mirroring uq_pm_dedup with NULLS DISTINCT
#     semantics; canonical wins, duplicates dropped.
#   * Defence-in-depth guard at line ~ keeps RAISE EXCEPTION — do NOT
#     downgrade (NFM-4092 / NFM-4099 ruling).
# ---------------------------------------------------------------------------

_FORWARD_DEDUP_SQL = """
    DO $BLK$
    DECLARE
        n_bad INT; n_skipped INT; n_winners INT; n_losers INT;
        n_moved INT; n_dupes INT; n_ds_dropped INT; n_src_deleted INT;
    BEGIN
        -- 1. Bad class = UUID-titled ONLY. Placeholder titles carry no
        --    identity evidence and are never dedup candidates.
        CREATE TEMP TABLE _canonical_map ON COMMIT DROP AS
        SELECT s.id AS bad_id, s.title::uuid AS canonical_id
        FROM data_sources s
        WHERE s.title ~
              '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
        SELECT COUNT(*) INTO n_bad FROM _canonical_map;

        -- 1a. SKIP (never delete) rows whose UUID title does not resolve
        --     to a live non-bad source, or that point at themselves.
        DELETE FROM _canonical_map cm
        WHERE cm.canonical_id = cm.bad_id
           OR NOT EXISTS (SELECT 1 FROM data_sources t WHERE t.id = cm.canonical_id)
           OR EXISTS (SELECT 1 FROM data_sources t WHERE t.id = cm.canonical_id
                      AND t.title ~
                          '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');
        GET DIAGNOSTICS n_skipped = ROW_COUNT;
        CREATE INDEX ON _canonical_map(bad_id);
        RAISE NOTICE 'NFM-4088: % uuid-titled sources, % unresolvable (skipped), % resolvable',
            n_bad, n_skipped, n_bad - n_skipped;

        -- 2. Exactly ONE winner dataset per (canonical_source, material).
        --    An existing canonical dataset always wins; otherwise the
        --    lowest bad dataset id wins.
        CREATE TEMP TABLE _bad_ds ON COMMIT DROP AS
        SELECT d.id AS bad_dataset_id, d.material_id, cm.canonical_id AS canonical_source_id
        FROM datasets d JOIN _canonical_map cm ON cm.bad_id = d.source_id;

        CREATE TEMP TABLE _target ON COMMIT DROP AS
        SELECT b.bad_dataset_id, b.canonical_source_id,
               COALESCE(e.existing_id, w.winner_id) AS canonical_dataset_id
        FROM _bad_ds b
        LEFT JOIN (SELECT source_id, material_id, MIN(id::text)::uuid AS existing_id
                     FROM datasets GROUP BY source_id, material_id) e
               ON e.source_id = b.canonical_source_id AND e.material_id = b.material_id
        JOIN (SELECT canonical_source_id, material_id, MIN(bad_dataset_id::text)::uuid AS winner_id
                FROM _bad_ds GROUP BY canonical_source_id, material_id) w
               ON w.canonical_source_id = b.canonical_source_id AND w.material_id = b.material_id;
        CREATE INDEX ON _target(bad_dataset_id);

        SELECT COUNT(*) INTO n_winners FROM _target WHERE bad_dataset_id = canonical_dataset_id;
        SELECT COUNT(*) INTO n_losers  FROM _target WHERE bad_dataset_id <> canonical_dataset_id;
        RAISE NOTICE 'NFM-4088: % datasets repointed in place, % folded into an existing dataset',
            n_winners, n_losers;

        -- 3. Repoint winners onto the canonical source.
        UPDATE datasets d SET source_id = t.canonical_source_id
        FROM _target t
        WHERE d.id = t.bad_dataset_id AND t.bad_dataset_id = t.canonical_dataset_id;

        -- 4. Move loser measurements that do not collide on uq_pm_dedup
        --    (dataset_id, property_type_id, conditions_hash, method).
        WITH ranked AS (
            SELECT pm.id, pm.property_type_id, pm.conditions_hash, pm.method,
                   t.canonical_dataset_id,
                   ROW_NUMBER() OVER (PARTITION BY t.canonical_dataset_id, pm.property_type_id,
                                                   pm.conditions_hash, pm.method
                                      ORDER BY pm.id) AS rn
            FROM property_measurements pm
            JOIN _target t ON t.bad_dataset_id = pm.dataset_id
            WHERE t.bad_dataset_id <> t.canonical_dataset_id
        )
        UPDATE property_measurements pm SET dataset_id = r.canonical_dataset_id, updated_at = NOW()
        FROM ranked r
        WHERE pm.id = r.id
          AND (r.rn = 1 OR r.conditions_hash IS NULL OR r.method IS NULL)
          AND NOT EXISTS (SELECT 1 FROM property_measurements c
                          WHERE c.dataset_id = r.canonical_dataset_id
                            AND c.property_type_id = r.property_type_id
                            AND c.conditions_hash = r.conditions_hash
                            AND c.method = r.method);
        GET DIAGNOSTICS n_moved = ROW_COUNT;

        -- 4a. Whatever is left on a loser dataset duplicates a row the
        --     canonical dataset already owns: canonical wins.
        DELETE FROM property_measurements pm USING _target t
        WHERE pm.dataset_id = t.bad_dataset_id AND t.bad_dataset_id <> t.canonical_dataset_id;
        GET DIAGNOSTICS n_dupes = ROW_COUNT;
        RAISE NOTICE 'NFM-4088: moved % measurements, dropped % duplicate measurements',
            n_moved, n_dupes;

        -- 5. Drop the now-empty loser datasets.
        DELETE FROM datasets d USING _target t
        WHERE d.id = t.bad_dataset_id AND t.bad_dataset_id <> t.canonical_dataset_id;
        GET DIAGNOSTICS n_ds_dropped = ROW_COUNT;

        -- 6. Defence in depth — RAISE EXCEPTION (NFM-4092 ruling).
        IF EXISTS (SELECT 1 FROM datasets ds JOIN _canonical_map cm ON cm.bad_id = ds.source_id) THEN
            RAISE EXCEPTION 'NFM-4088: refusing DELETE — datasets still reference bad sources';
        END IF;
        RAISE NOTICE 'NFM-4088: guard passed; dropped % folded datasets', n_ds_dropped;

        DELETE FROM data_sources WHERE id IN (SELECT bad_id FROM _canonical_map);
        GET DIAGNOSTICS n_src_deleted = ROW_COUNT;
        RAISE NOTICE 'NFM-4088: deleted % uuid-titled data_sources rows', n_src_deleted;
    END $BLK$;
"""


def upgrade() -> None:
    """D2 dedup migration — collapse UUID-titled ``data_sources`` rows to canonical.

    Bad class = UUID-titled rows only. ``title::uuid`` is the
    deterministic canonical resolution; unresolvable rows are kept
    (skipped, not deleted). One winner dataset per
    ``(canonical_source, material)`` keeps ``uq_datasets_source_material``
    intact; single measurement move mirroring ``uq_pm_dedup`` keeps
    ``uq_pm_dedup`` intact. Defence-in-depth guard stays
    ``RAISE EXCEPTION`` per NFM-4092 ruling.
    """
    bind = op.get_bind()

    # Refresh snapshot tables so a downgrade has a fresh, exact copy
    # of the pre-migration state of the three target tables.
    bind.execute(sa.text("DROP TABLE IF EXISTS data_sources_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS datasets_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS property_measurements_backup_070"))
    bind.execute(sa.text("CREATE TABLE data_sources_backup_070 AS SELECT * FROM data_sources"))
    bind.execute(sa.text("CREATE TABLE datasets_backup_070 AS SELECT * FROM datasets"))
    bind.execute(sa.text("CREATE TABLE property_measurements_backup_070 AS SELECT * FROM property_measurements"))

    # Forward dedup — single plpgsql block, no bind parameters.
    bind.execute(sa.text(_FORWARD_DEDUP_SQL))


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Restore the pre-migration state from the backup tables.

    All three target tables are truncated first because the dedup
    has changed primary-key ownerships (deletions of duplicates,
    repointings of source_ids).  Insert back from the snapshots.
    """
    bind = op.get_bind()

    bind.execute(sa.text("DELETE FROM measurement_conditions"))
    bind.execute(sa.text("DELETE FROM property_measurements"))
    bind.execute(sa.text("DELETE FROM data_source_authors"))
    bind.execute(sa.text("DELETE FROM datasets"))
    bind.execute(sa.text("DELETE FROM data_sources"))

    bind.execute(sa.text("INSERT INTO data_sources SELECT * FROM data_sources_backup_070"))
    bind.execute(sa.text("INSERT INTO datasets SELECT * FROM datasets_backup_070"))
    bind.execute(
        sa.text(
            """
            INSERT INTO property_measurements
            SELECT * FROM property_measurements_backup_070
            """
        )
    )

    bind.execute(sa.text("DROP TABLE IF EXISTS data_sources_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS datasets_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS property_measurements_backup_070"))
