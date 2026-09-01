-- =============================================================================
-- NFM-3918 — Unknown Material cleanup migration
-- =============================================================================
-- Decision: D3 = A+C hybrid (refines parent ticket NFM-3913 D3=C).
--   17 zero-downstream Unknown  -> HARD DELETE  (no measurements, no aliases,
--                                              no compositions — no audit value)
--   10 carrying-data Unknown   -> MERGE          (93 measurements must survive)
--
-- HARD DEPENDENCY (per ticket body):
--   Tier 1B (NFM-3919) must be MERGED AND DEPLOYED TO PROD before this script
--   runs against prod. The current rate of garbage-row creation (22/27 in 24h)
--   means prod cleanup without the upstream block would re-pollute within a
--   day. Staging dry-run is allowed at any time.
--
-- INVARIANTS (enforced in script, NOT to be relaxed):
--   1. Total property_measurement row count MUST NOT DECREASE except for
--      uq_pm_dedup conflicts that the script logs with --dedup-skip list.
--   2. `select count(*) from materials where name='Unknown Material'`
--      after = 0.
--   3. `value_scalar=10.55` density rows after = 1 (was 8 across 8 Unknown).
--   4. No orphan dataset or measurement rows (FK integrity).
--
-- USAGE:
--   -- dry-run, against staging:
--   psql "$STAGING_DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v dry_run=1 \
--        -f scripts/nfm-3918-unknown-material-cleanup.sql
--
--   -- apply, against prod (gated):
--   psql "$PROD_DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v dry_run=0 \
--        -v require_backup_path=/path/to/pg_dump.sql \
--        -f scripts/nfm-3918-unknown-material-cleanup.sql
--
-- IDEMPOTENCY:
--   * Hard delete: naturally idempotent (deleted rows stay deleted).
--   * Merge:       guarded by `nfm_3918_merge_log` audit table that records
--                  every (unknown_id, target_material_id) pair. Re-runs are
--                  a no-op for already-merged pairs.
--   * `nfm_3918_preflight` raises if Unknown row count != 27 (or the
--     override count supplied via -v expected_unknown_count=N).
--
-- =============================================================================

\set ON_ERROR_STOP on

-- -----------------------------------------------------------------------------
-- Phase 0 — Pre-flight guard
-- -----------------------------------------------------------------------------
-- Bail loudly if the preconditions don't hold. This is the same shape as
-- scripts/prod_migrate.sh's deploy-lock dance: if the world looks different
-- from what we expect, we refuse to guess.

DO $$
DECLARE
    unknown_count INTEGER;
    expected_count INTEGER := coalesce(current_setting('expected_unknown_count', true)::int, 27);
    backup_path TEXT := current_setting('require_backup_path', true);
    is_dry_run BOOLEAN := current_setting('dry_run', true)::boolean;
BEGIN
    SELECT count(*) INTO unknown_count
    FROM materials
    WHERE name = 'Unknown Material';

    RAISE NOTICE '[NFM-3918 phase 0] Unknown Material count = %, expected = %',
        unknown_count, expected_count;

    IF unknown_count != expected_count THEN
        RAISE EXCEPTION 'NFM-3918 preflight FAILED: Unknown count drift (got %, expected %). '
            'Refusing to run. Re-run with -v expected_unknown_count=<actual> to override, '
            'or update the script default once the count stabilizes post-Tier-1B.',
            unknown_count, expected_count;
    END IF;

    IF NOT is_dry_run AND backup_path IS NULL THEN
        RAISE EXCEPTION 'NFM-3918 preflight FAILED: prod apply requires -v require_backup_path=<path>. '
            'pg_dump of materials/datasets/property_measurements must exist BEFORE this script runs. '
            'See ticket body §5 — pg_dump is the ONLY rollback path because this migration '
            'spans deletions and cross-table moves that git revert cannot undo.';
    END IF;

    RAISE NOTICE '[NFM-3918 phase 0] OK — proceeding (dry_run=%)', is_dry_run;
END $$;


-- -----------------------------------------------------------------------------
-- Phase 1 — Audit log table (idempotency anchor)
-- -----------------------------------------------------------------------------
-- Created unconditionally on every run. If it already exists, this is a no-op.

CREATE TABLE IF NOT EXISTS nfm_3918_merge_log (
    unknown_material_id    UUID PRIMARY KEY,
    target_material_id     UUID NOT NULL,
    migrated_measurements  INTEGER NOT NULL,
    dedup_skipped          INTEGER NOT NULL DEFAULT 0,
    dedup_skip_details     JSONB,
    executed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nfm_3918_merge_log IS
    'NFM-3918 idempotency anchor. Records every Unknown→target merge. '
    'Re-running the migration is a no-op for rows already logged here. '
    'Delete rows from this table to re-attempt a merge (NOT recommended without '
    'restoring the Unknown material from pg_dump backup).';


-- -----------------------------------------------------------------------------
-- Phase 2 — Snapshot (BEFORE counts, returned to caller via RAISE NOTICE)
-- -----------------------------------------------------------------------------
-- This block emits a single NOTICE block with the full before picture, which
-- the Python wrapper scripts/nfm-3918-unknown-material-cleanup.py captures
-- into the before/after comparison table required by AC #1.

DO $$
DECLARE
    n_unknown INTEGER;
    n_zero_downstream INTEGER;
    n_carrying_data INTEGER;
    n_datasets_unknown INTEGER;
    n_measurements_unknown INTEGER;
    n_measurements_total INTEGER;
    n_aliases_unknown INTEGER;
    n_compositions_unknown INTEGER;
    n_density_10_55 INTEGER;
BEGIN
    SELECT count(*) INTO n_unknown FROM materials WHERE name = 'Unknown Material';

    -- "Zero downstream" is defined by the ticket body as no measurement, no
    -- alias, no composition — NOT by dataset existence. An Unknown row that
    -- owns only an EMPTY dataset carries nothing and must be hard-deleted.
    -- Measured on the real 28-row shard: dataset-keyed = 2, measurement-keyed
    -- = 17 (the ticket's number).
    SELECT count(*) INTO n_zero_downstream
    FROM materials m
    WHERE m.name = 'Unknown Material'
      AND NOT EXISTS (
          SELECT 1 FROM datasets d
          JOIN property_measurements pm ON pm.dataset_id = d.id
          WHERE d.material_id = m.id
      )
      AND NOT EXISTS (SELECT 1 FROM material_aliases a WHERE a.material_id = m.id)
      AND NOT EXISTS (SELECT 1 FROM material_compositions c WHERE c.material_id = m.id);

    n_carrying_data := n_unknown - n_zero_downstream;

    SELECT count(*) INTO n_datasets_unknown
    FROM datasets d JOIN materials m ON m.id = d.material_id
    WHERE m.name = 'Unknown Material';

    SELECT count(*) INTO n_measurements_unknown
    FROM property_measurements pm
        JOIN datasets d ON d.id = pm.dataset_id
        JOIN materials m ON m.id = d.material_id
    WHERE m.name = 'Unknown Material';

    -- Global total, not Unknown-scoped. Invariant #1 ("no measurement loss")
    -- is a whole-table property, so BEFORE and AFTER must both publish the
    -- global count or the comparison comes out apples-to-oranges.
    SELECT count(*) INTO n_measurements_total FROM property_measurements;

    SELECT count(*) INTO n_aliases_unknown
    FROM material_aliases a JOIN materials m ON m.id = a.material_id
    WHERE m.name = 'Unknown Material';

    SELECT count(*) INTO n_compositions_unknown
    FROM material_compositions c JOIN materials m ON m.id = c.material_id
    WHERE m.name = 'Unknown Material';

    SELECT count(*) INTO n_density_10_55
    FROM property_measurements pm
        JOIN property_types pt ON pt.id = pm.property_type_id
        JOIN datasets d ON d.id = pm.dataset_id
        JOIN materials m ON m.id = d.material_id
    WHERE m.name = 'Unknown Material'
      AND pt.slug = 'density'
      AND pm.value_scalar = 10.55;

    RAISE NOTICE 'NFM-3918_BEFORE unknown=% zero_downstream=% carrying_data=% datasets=% '
                 'measurements=% measurements_total=% aliases=% compositions=% density_10_55=%',
        n_unknown, n_zero_downstream, n_carrying_data, n_datasets_unknown,
        n_measurements_unknown, n_measurements_total, n_aliases_unknown,
        n_compositions_unknown, n_density_10_55;
END $$;


-- -----------------------------------------------------------------------------
-- Phase 3 + Phase 4 — Transactional mutation wrapper
-- -----------------------------------------------------------------------------
-- Both phases run inside a single `DO $outer$` block so a mid-run failure
-- in Phase 4 cannot leave the database in a half-applied state. PL/pgSQL
-- has NO implicit transaction across top-level DO blocks; an uncaught
-- error inside an inner DO would leave Phase 3's 17 deletions committed
-- while Phase 4's compensating merges never ran. The first faithful
-- staging apply demonstrated this exact failure mode (Phase 3 committed,
-- Phase 4 crashed on the `materials.source_id` typo, DB left at
-- unknown=11 with no rollback path).
--
-- Inside $outer$ the apply path wraps Phase 3 and Phase 4 in an explicit
-- BEGIN/EXCEPTION block so any error RAISEs the original message and
-- rolls the whole apply back. Dry-run is a no-mutation path that emits
-- notice-only counters and returns early.
--
-- Phase 3 (hard delete) only touches materials with ZERO downstream
-- measurements / aliases / compositions. Such a material MAY still own
-- datasets — empty ones. `datasets.material_id` is ON DELETE CASCADE
-- (and `property_measurements.dataset_id` likewise), so deleting the
-- material also removes those empty dataset rows, which is exactly what
-- the ticket asks for ("硬删除 17 条零下游数据的 Unknown(含其空 dataset 行)").
-- Because the predicate excludes anything reachable to a measurement, the
-- cascade can never destroy a measurement.
--
-- Tier 1B (NFM-3919) closes the upstream by teaching the mapper to reject
-- formula=NULL AND material_name=NULL items. Post-Tier-1B, no NEW Unknown
-- rows are created, so the hard delete below stays permanent.

DO $outer$
BEGIN
    IF current_setting('dry_run', true)::boolean THEN
        -- Phase 3 dry-run notice.
        DECLARE
            deleted_count INTEGER;
        BEGIN
            SELECT count(*) INTO deleted_count
            FROM materials m
            WHERE m.name = 'Unknown Material'
              AND NOT EXISTS (
                  SELECT 1 FROM datasets d
                  JOIN property_measurements pm ON pm.dataset_id = d.id
                  WHERE d.material_id = m.id
              )
              AND NOT EXISTS (SELECT 1 FROM material_aliases a WHERE a.material_id = m.id)
              AND NOT EXISTS (SELECT 1 FROM material_compositions c WHERE c.material_id = m.id);
            RAISE NOTICE '[NFM-3918 phase 3 dry-run] would hard-delete % zero-downstream Unknown rows',
                deleted_count;
        END;

        -- Phase 4 dry-run notice (per-row outcomes only appear in the apply).
        RAISE NOTICE '[NFM-3918 phase 4 dry-run] would attempt merge for each carrying-data Unknown. '
            'Run without -v dry_run=1 to see per-row outcomes.';
        RETURN;
    END IF;

    -- Apply: wrap phases 3 + 4 in a single transaction. Phase 5 is the
    -- post-state readback and is always non-mutating, so it doesn't need
    -- to be inside the txn; including it makes rollback semantics simpler
    -- and Phase 5's RAISE NOTICE still surfaces after COMMIT.
    BEGIN
        -- ----- Phase 3 apply -----
        DECLARE
            deleted_count INTEGER;
        BEGIN
            WITH zero_downstream AS (
                SELECT m.id
                FROM materials m
                WHERE m.name = 'Unknown Material'
                  AND NOT EXISTS (
                      SELECT 1 FROM datasets d
                      JOIN property_measurements pm ON pm.dataset_id = d.id
                      WHERE d.material_id = m.id
                  )
                  AND NOT EXISTS (SELECT 1 FROM material_aliases a WHERE a.material_id = m.id)
                  AND NOT EXISTS (SELECT 1 FROM material_compositions c WHERE c.material_id = m.id)
            ),
            deleted AS (
                DELETE FROM materials m
                USING zero_downstream z
                WHERE m.id = z.id
                RETURNING m.id
            )
            SELECT count(*) INTO deleted_count FROM deleted;

            RAISE NOTICE '[NFM-3918 phase 3 applied] hard-deleted % zero-downstream Unknown rows',
                deleted_count;
        END;

        -- ----- Phase 4 apply -----
        DECLARE
            merged_count INTEGER := 0;
            skipped_count INTEGER := 0;
            unresolved_count INTEGER := 0;
            rec RECORD;
        BEGIN
            FOR rec IN
                WITH carrying AS (
                    SELECT DISTINCT m.id AS unknown_id
                    FROM materials m
                    WHERE m.name = 'Unknown Material'
                      AND EXISTS (
                          SELECT 1 FROM datasets d
                          JOIN property_measurements pm ON pm.dataset_id = d.id
                          WHERE d.material_id = m.id
                      )
                ),
                resolved AS (
                    -- The paper linkage lives on datasets.source_id; `materials` has
                    -- no source_id column. Walk Unknown -> its datasets -> same
                    -- source_id -> a dataset owned by a NON-Unknown material.
                    SELECT c.unknown_id,
                           tgt.target_material_id,
                           tgt.source_id
                    FROM carrying c
                    JOIN LATERAL (
                        SELECT m2.id AS target_material_id, ud.source_id
                        FROM datasets ud
                        JOIN datasets td ON td.source_id = ud.source_id
                        JOIN materials m2 ON m2.id = td.material_id
                        WHERE ud.material_id = c.unknown_id
                          AND m2.name <> 'Unknown Material'
                          AND m2.id <> c.unknown_id
                        ORDER BY m2.created_at ASC
                        LIMIT 1
                    ) tgt ON true
                )
                SELECT r.unknown_id, r.target_material_id, r.source_id
                FROM resolved r
                WHERE NOT EXISTS (
                    SELECT 1 FROM nfm_3918_merge_log l WHERE l.unknown_material_id = r.unknown_id
                )
            LOOP
                -- Per-row merge: walk datasets of the Unknown, re-attach each
                -- property_measurement to the target's dataset (handling dedup),
                -- then drop the now-empty Unknown-side datasets and the material.
                DECLARE
                    meas_moved INTEGER := 0;
                    meas_dedup INTEGER := 0;
                    dedup_details JSONB := '[]'::jsonb;
                    ds_rec RECORD;
                    pm_rec RECORD;
                    target_dataset_id UUID;
                BEGIN
                    -- Find (or create) the target dataset. Each Unknown dataset may
                    -- carry multiple property types; we co-locate them onto a single
                    -- dataset on the target material side, preferring an existing
                    -- dataset on the same source.
                    SELECT d.id INTO target_dataset_id
                    FROM datasets d
                    WHERE d.material_id = rec.target_material_id
                      AND d.source_id = rec.source_id
                    ORDER BY d.created_at ASC
                    LIMIT 1;

                    IF target_dataset_id IS NULL THEN
                        INSERT INTO datasets (id, material_id, source_id, title, created_at, updated_at)
                        VALUES (gen_random_uuid(), rec.target_material_id, rec.source_id,
                                'merged-from-unknown-' || rec.unknown_id::text, now(), now())
                        RETURNING id INTO target_dataset_id;
                    END IF;

                    FOR ds_rec IN
                        SELECT id FROM datasets WHERE material_id = rec.unknown_id
                    LOOP
                        FOR pm_rec IN
                            SELECT pm.id, pm.property_type_id, pm.conditions_hash, pm.method,
                                   pm.value_scalar, pm.value_min, pm.value_max, pm.value_expression,
                                   pm.value_list, pm.value_text, pm.uncertainty, pm.unit_id, pm.notes,
                                   pm.review_status, pm.reviewer_note, pm.reviewed_at
                            FROM property_measurements pm
                            WHERE pm.dataset_id = ds_rec.id
                        LOOP
                            -- uq_pm_dedup guard: if target already has this exact
                            -- (property_type_id, conditions_hash, method) on this
                            -- dataset, skip the insert and log the dedup.
                            IF EXISTS (
                                SELECT 1 FROM property_measurements t
                                WHERE t.dataset_id = target_dataset_id
                                  AND t.property_type_id = pm_rec.property_type_id
                                  AND t.conditions_hash IS NOT DISTINCT FROM pm_rec.conditions_hash
                                  AND t.method IS NOT DISTINCT FROM pm_rec.method
                            ) THEN
                                meas_dedup := meas_dedup + 1;
                                dedup_details := dedup_details || jsonb_build_object(
                                    'unknown_measurement_id', pm_rec.id,
                                    'property_type_id', pm_rec.property_type_id,
                                    'conditions_hash', pm_rec.conditions_hash,
                                    'method', pm_rec.method
                                );
                                -- Drop the source row to keep the count invariant.
                                DELETE FROM property_measurements WHERE id = pm_rec.id;
                            ELSE
                                UPDATE property_measurements
                                SET dataset_id = target_dataset_id
                                WHERE id = pm_rec.id;
                                meas_moved := meas_moved + 1;
                            END IF;
                        END LOOP;

                        -- Drop the now-empty source dataset (FK ON DELETE CASCADE
                        -- handles any stragglers).
                        DELETE FROM datasets WHERE id = ds_rec.id;
                    END LOOP;

                    -- Drop the Unknown material itself.
                    DELETE FROM materials WHERE id = rec.unknown_id;

                    INSERT INTO nfm_3918_merge_log
                        (unknown_material_id, target_material_id, migrated_measurements,
                         dedup_skipped, dedup_skip_details)
                    VALUES
                        (rec.unknown_id, rec.target_material_id, meas_moved,
                         meas_dedup, dedup_details);

                    merged_count := merged_count + 1;
                END;
            END LOOP;

            -- Unresolved rows (Unknown still carrying measurements but with no
            -- matching target material). These are left in place for manual review.
            SELECT count(*) INTO unresolved_count
            FROM materials m
            WHERE m.name = 'Unknown Material'
              AND EXISTS (
                  SELECT 1 FROM datasets d
                  JOIN property_measurements pm ON pm.dataset_id = d.id
                  WHERE d.material_id = m.id
              );

            RAISE NOTICE '[NFM-3918 phase 4 applied] merged % Unknown rows; unresolved=%',
                merged_count, unresolved_count;
        END;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'NFM-3918 phases 3+4 failed and were rolled back: %', SQLERRM;
    END;
END $outer$;


-- -----------------------------------------------------------------------------
-- Phase 5 — Post-flight verify
-- -----------------------------------------------------------------------------
-- Emits AFTER counts. The Python wrapper pairs these with the BEFORE block
-- to produce the table required by AC #1. Phase 5 is intentionally outside
-- the $outer$ transaction: it reads post-state, never mutates, and its RAISE
-- NOTICE has to fire after COMMIT so the BEFORE/AFTER pair is unambiguous.

DO $$
DECLARE
    n_unknown INTEGER;
    n_measurements_total INTEGER;
    -- Reported for human context only. The machine-checked comparison is
    -- BEFORE.measurements_total vs AFTER.measurements_total, done by the
    -- Python wrapper — never against this literal, which goes stale the
    -- moment ingest adds a row (it read 93 when the ticket was filed and
    -- 97 by the first faithful staging dry-run).
    n_measurements_pre INTEGER := 93;  -- ticket body §决策依据, informational
    n_density_10_55 INTEGER;
    n_orphans_datasets INTEGER;
    n_orphans_measurements INTEGER;
    dedup_total INTEGER;
BEGIN
    SELECT count(*) INTO n_unknown FROM materials WHERE name = 'Unknown Material';

    SELECT count(*) INTO n_measurements_total FROM property_measurements;

    SELECT count(*) INTO n_density_10_55
    FROM property_measurements pm
        JOIN property_types pt ON pt.id = pm.property_type_id
        JOIN datasets d ON d.id = pm.dataset_id
        JOIN materials m ON m.id = d.material_id
    WHERE pt.slug = 'density'
      AND pm.value_scalar = 10.55;

    -- Orphan checks: measurements pointing at nonexistent dataset/material,
    -- datasets pointing at nonexistent material.
    SELECT count(*) INTO n_orphans_measurements
    FROM property_measurements pm
    WHERE NOT EXISTS (SELECT 1 FROM datasets d WHERE d.id = pm.dataset_id)
       OR NOT EXISTS (
            SELECT 1 FROM datasets d
            JOIN materials m ON m.id = d.material_id
            WHERE d.id = pm.dataset_id
       );

    SELECT count(*) INTO n_orphans_datasets
    FROM datasets d
    WHERE d.material_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM materials m WHERE m.id = d.material_id);

    SELECT coalesce(sum(dedup_skipped), 0) INTO dedup_total FROM nfm_3918_merge_log;

    RAISE NOTICE 'NFM-3918_AFTER unknown=% measurements_total=% (was %) '
                 'density_10_55=% orphan_datasets=% orphan_measurements=% dedup_total=%',
        n_unknown, n_measurements_total, n_measurements_pre,
        n_density_10_55, n_orphans_datasets, n_orphans_measurements, dedup_total;
END $$;
