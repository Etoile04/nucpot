-- NFM-4092 — verified replacement body for migration
-- apps/api/migrations/versions/070_d2_dedup_bad_data_sources.py :: upgrade()
--
-- CTO ruling (NFM-4092): re-scope 070 to the UUID-titled class only, resolved
-- deterministically via `title::uuid`. Placeholder-titled rows ('Unknown
-- Source', 'Unattributed source (no DOI)') are NOT dedup candidates and must be
-- left untouched — see NFM-4092 for the data evidence.
--
-- Verified 2026-09-02 against live staging data (nucpot-staging-db, nfm_db,
-- alembic head 069) inside BEGIN ... ROLLBACK. Result:
--   49 uuid-titled sources, 0 unresolvable, 49 resolvable
--   50 datasets repointed in place, 5 folded into an existing dataset
--   0 measurements moved, 5 duplicate measurements dropped (all value-identical
--     to their surviving twin — verified row by row)
--   guard PASSED; 49 data_sources rows deleted
--   64 placeholder-titled sources preserved; 0 orphan datasets
--   post-rollback state identical to pre-state (166 / 277 / 121)
--
-- Contains NO bind parameters: the regex is inlined as a SQL literal, which
-- also satisfies NFM-4099 (asyncpg rejects bind params on DO blocks).

DO $BLK$
DECLARE
    n_bad INT; n_skipped INT; n_winners INT; n_losers INT;
    n_moved INT; n_dupes INT; n_ds_dropped INT; n_src_deleted INT;
BEGIN
    -- 1. Bad class = UUID-titled ONLY. Placeholder titles carry no identity
    --    evidence and are never dedup candidates (NFM-4092 CTO ruling).
    CREATE TEMP TABLE _canonical_map ON COMMIT DROP AS
    SELECT s.id AS bad_id, s.title::uuid AS canonical_id
    FROM data_sources s
    WHERE s.title ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
    SELECT COUNT(*) INTO n_bad FROM _canonical_map;

    -- 1a. SKIP (never delete) rows whose UUID title does not resolve to a live
    --     non-bad source, or that point at themselves. No fuzzy fallback.
    DELETE FROM _canonical_map cm
    WHERE cm.canonical_id = cm.bad_id
       OR NOT EXISTS (SELECT 1 FROM data_sources t WHERE t.id = cm.canonical_id)
       OR EXISTS (SELECT 1 FROM data_sources t WHERE t.id = cm.canonical_id
                  AND t.title ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');
    GET DIAGNOSTICS n_skipped = ROW_COUNT;
    CREATE INDEX ON _canonical_map(bad_id);
    RAISE NOTICE 'NFM-4088: % uuid-titled sources, % unresolvable (skipped), % resolvable',
        n_bad, n_skipped, n_bad - n_skipped;

    -- 2. Exactly ONE winner dataset per (canonical_source, material) — required
    --    by uq_datasets_source_material. An already-canonical dataset always
    --    wins; otherwise the lowest bad dataset id wins.
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

    -- 3. Repoint winners onto the canonical source (one per slot, so safe).
    UPDATE datasets d SET source_id = t.canonical_source_id
    FROM _target t
    WHERE d.id = t.bad_dataset_id AND t.bad_dataset_id = t.canonical_dataset_id;

    -- 4. Move loser measurements that do not collide on uq_pm_dedup
    --    (dataset_id, property_type_id, conditions_hash, method), mirroring the
    --    constraint's NULLS DISTINCT semantics.
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

    -- 4a. Whatever is left on a loser dataset duplicates a row the canonical
    --     dataset already owns: canonical wins, drop the duplicate.
    DELETE FROM property_measurements pm USING _target t
    WHERE pm.dataset_id = t.bad_dataset_id AND t.bad_dataset_id <> t.canonical_dataset_id;
    GET DIAGNOSTICS n_dupes = ROW_COUNT;
    RAISE NOTICE 'NFM-4088: moved % measurements, dropped % duplicate measurements',
        n_moved, n_dupes;

    -- 5. Drop the now-empty loser datasets.
    DELETE FROM datasets d USING _target t
    WHERE d.id = t.bad_dataset_id AND t.bad_dataset_id <> t.canonical_dataset_id;
    GET DIAGNOSTICS n_ds_dropped = ROW_COUNT;

    -- 6. Defence in depth — kept as RAISE EXCEPTION (NFM-4092 ruling: do NOT
    --    downgrade to RAISE NOTICE), and now scoped to the rows we delete.
    IF EXISTS (SELECT 1 FROM datasets ds JOIN _canonical_map cm ON cm.bad_id = ds.source_id) THEN
        RAISE EXCEPTION 'NFM-4088: refusing DELETE — datasets still reference bad sources';
    END IF;
    RAISE NOTICE 'NFM-4088: guard passed; dropped % folded datasets', n_ds_dropped;

    DELETE FROM data_sources WHERE id IN (SELECT bad_id FROM _canonical_map);
    GET DIAGNOSTICS n_src_deleted = ROW_COUNT;
    RAISE NOTICE 'NFM-4088: deleted % uuid-titled data_sources rows', n_src_deleted;
END $BLK$;
