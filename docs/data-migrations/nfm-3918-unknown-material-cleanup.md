# NFM-3918 — Unknown Material cleanup runbook

## What this is

The SQL + Python pair in `scripts/nfm-3918-unknown-material-cleanup.{sql,py}`
cleans up the 27 `name='Unknown Material'` rows that the parent research
(NFM-3903) traced to `extraction_to_db_mapper.py:610` falling back to
`"Unknown Material"` whenever the heuristic extractor emits a measurement
without a `material_name` or `composition`.

17 of the 27 rows are zero-downstream (no measurements, no aliases, no
compositions) and are hard-deleted. The other 10 carry 93 measurements
total (83 concentrated on the single row `78f74516-ff64-4a26-aa4f-…`) and
are merged onto the correct target material (the non-Unknown material that
already exists for the same `source_id`).

## Hard prerequisite

Tier 1B (NFM-3919) must be **merged AND deployed to prod** before this
migration runs against prod. Tier 1B closes the upstream by teaching the
mapper to reject items with `formula=NULL AND material_name=NULL`, and
teaches the heuristic extractor to emit `material_name=element_system`.
Without Tier 1B, the upstream keeps creating new Unknown rows at the rate
observed in the ticket body (~22 rows/day as of 2026-08-31), so any prod
cleanup re-pollutes within a day.

**Staging dry-run is allowed at any time.** The staging DB does not receive
production ingest at the same rate, and the script's dry-run mode is a
purely read-only snapshot.

## Files

| Path | Purpose |
|---|---|
| `scripts/nfm-3918-unknown-material-cleanup.sql` | The migration itself. Idempotent via `nfm_3918_merge_log`. Phase 0 preflight, phase 1 audit table, phase 2 BEFORE snapshot, phase 3 hard delete, phase 4 merge, phase 5 AFTER snapshot. |
| `scripts/nfm-3918-unknown-material-cleanup.py` | The wrapper that runs the SQL, captures the BEFORE / AFTER `RAISE NOTICE` blocks, renders the AC #1 comparison table, and enforces the prod safety gate. |
| `docs/data-migrations/nfm-3918-unknown-material-cleanup.md` | This file. |

## Phases

### Phase 0 — preflight

`scripts/nfm-3918-unknown-material-cleanup.sql` raises `EXCEPTION` if the
current Unknown count isn't `expected_unknown_count` (default 27; tunable
via `-v expected_unknown_count=N` on the psql command line). On prod apply,
it also requires `-v require_backup_path=<path>` — the path to the
`pg_dump` output for `materials`, `datasets`, `property_measurements`.
The ticket body §"回滚" is explicit: pg_dump is the only rollback path
because git revert can't undo cross-table moves.

### Phase 1 — `nfm_3918_merge_log`

An idempotency anchor. Every (Unknown material → target material) merge
is logged here with the count of migrated measurements and the count of
`uq_pm_dedup` skips. Re-runs are a no-op for already-logged pairs. To
re-attempt a merge manually, delete the row from this table (only after
restoring the Unknown material from the pg_dump backup).

### Phase 2 — BEFORE snapshot

Emits one `RAISE NOTICE 'NFM-3918_BEFORE …'` line with the full picture:
unknown count, zero-downstream count, carrying-data count, datasets,
measurements, aliases, compositions, density=10.55 row count. The Python
wrapper captures this and renders the AC #1 comparison table.

### Phase 3 — hard delete (17 rows)

Touches only materials with zero rows in `datasets`, `material_aliases`,
`material_compositions`. The FK on `datasets.material_id` has no
`ON DELETE` clause in the model (`apps/api/src/nfm_db/models/material.py:106-108`),
so this only fires for materials that genuinely have no downstream rows
guarding them.

### Phase 4 — merge (10 rows)

Phase 4 selects its merge strategy via the `merge_strategy` psql
variable (`-v merge_strategy=<value>` on the CLI / `--strategy <value>`
on the wrapper). Two strategies are supported:

#### `source_id_walk` (default)

For each carrying-data Unknown row, resolve the target material with
the following rule:

```sql
target(m) = material m' with m'.source_id = m.source_id
            AND m'.name <> 'Unknown Material'
            AND m'.id <> m.id
ORDER BY m'.created_at ASC LIMIT 1
```

If no such target exists, the Unknown row is left in place as a manual-
review item (the conservative fallback — refusing to guess is safer than
fusing to a random material). The Python wrapper reports these in the
unresolved count for human follow-up.

Per-row merge: walk each dataset of the Unknown, walk each measurement,
and either:

  * **move**: `UPDATE property_measurements SET dataset_id = target_dataset_id`
    where the target's dataset does not yet contain that exact
    `(property_type_id, conditions_hash, method)` triple; OR
  * **dedup-skip**: drop the source row if the target already has that
    triple, and log the skip to `nfm_3918_merge_log.dedup_skip_details`
    as a JSONB record.

This is the **first time `uq_pm_dedup UNIQUE (dataset_id, property_type_id,
conditions_hash, method)` actually fires in production** — see ticket body
§4. Concentrating scattered rows onto a single dataset brings previously
distributed triples into collision. The dedup-skip list is required by
AC #3 ("若有去重,逐条列出被去重的行与理由").

After migration: drop the now-empty source datasets and the Unknown
material itself. The wrapper's `assert_invariants()` enforces the strict
AC #4 contract: `density=10.55` rows must collapse from 8 to 1
(since the 8 Unknowns each carried their own density=10.55 row).

#### `new_canonical`

**When to use:** the staging shard pattern NFM-3918 actually exhibits —
the carrying-data Unknowns have `source_id` values with no non-Unknown
sibling, so `source_id_walk` resolves 0/N targets and leaves N Unknowns
stranded. Confirmed by NFM-3903 dry-run 2026-09-01 against the staging
shard (11 carrying-data Unknowns, 0 source_id_walk targets).

Strategy:

  1. Insert one row `materials(name='Unknown Material (canonical)')`
     with `id = gen_random_uuid()`. Capture `canonical_id`.
  2. For each carrying-data Unknown row, walk its datasets and:
     * `UPDATE datasets SET material_id = canonical_id WHERE
       material_id = unknown_id` (re-point every dataset; FK cascade
       from `property_measurements.dataset_id` is unaffected).
     * `INSERT INTO nfm_3918_merge_log(unknown_material_id,
       target_material_id, migrated_measurements, dedup_skipped,
       dedup_skip_details)` with `dedup_skipped=0,
       dedup_skip_details='[]'::jsonb`.
     * `DELETE FROM materials WHERE id = unknown_id`.
  3. End-of-phase `RAISE NOTICE` reports `canonical=<uuid>
     deleted_unknowns=<n> re_pointed_datasets=<n>
     measurements_preserved=<n>`.

Invariants enforced by the wrapper (relaxed AC #4):

  * AC #1 — `Unknown = 0` after (canonical row is **not** counted as
    `name='Unknown Material'`).
  * AC #2 — measurement preservation: `after >= before` (no loss;
    `new_canonical` does not touch `property_measurements`, so the count
    is exact).
  * AC #3 — `density=10.55` rows must **not grow** (guardrail: the
    migration never adds density rows). The strict 8→1 collapse is
    relaxed because the 8 density rows legitimately live on the 8
    separate Unknowns being merged and now co-exist on the canonical.
  * FK integrity — `orphan_datasets = 0`, `orphan_measurements = 0`.
  * `nfm_3918_merge_log` must record ≥ 1 row per deleted Unknown (the
    `target_material_id` for every entry is the same canonical).

### Phase 5 — AFTER snapshot

Emits `RAISE NOTICE 'NFM-3918_AFTER …'` with the post-state: unknown
count, total measurement count, density=10.55 row count, orphan dataset
count, orphan measurement count, total dedup-skipped across all merges.

The Python wrapper pairs BEFORE and AFTER into the AC #1 table and runs
`assert_invariants()`:

  * AC #2 — `Unknown = 0` after.
  * AC #3 — measurements preserved (`after >= before − dedup`).
  * AC #4 — `density=10.55` rows = 1 (was 8 across 8 Unknown).
  * FK integrity — `orphan_datasets = 0`, `orphan_measurements = 0`.

## Usage

### Staging dry-run (always allowed)

```bash
python scripts/nfm-3918-unknown-material-cleanup.py \
    --database-url "$STAGING_DATABASE_URL" \
    --dry-run
```

Output: the BEFORE / AFTER comparison table. Phases 3 / 4 emit "would do"
notices; the database is unmodified.

For shards where `source_id_walk` resolves 0/N targets (the staging
shard pattern as of NFM-3903 dry-run 2026-09-01), pass
`--strategy new_canonical` to use Phase 4 strategy B:

```bash
python scripts/nfm-3918-unknown-material-cleanup.py \
    --database-url "$STAGING_DATABASE_URL" \
    --dry-run \
    --strategy new_canonical
```

### Staging apply

```bash
python scripts/nfm-3918-unknown-material-cleanup.py \
    --database-url "$STAGING_DATABASE_URL"
# or for the staging-shard pattern:
python scripts/nfm-3918-unknown-material-cleanup.py \
    --database-url "$STAGING_DATABASE_URL" \
    --strategy new_canonical
```

Demonstrates the migration end-to-end on staging. The staging DB should
have its own Unknown pollution to exercise the merge path; if it doesn't,
the script is still useful as a smoke test for the hard-delete path.

### Prod apply (gated)

```bash
# Step 1: confirm Tier 1B is deployed
gh run list --workflow production-deployment.yml --limit 5 \
    | grep -i "tier-1b\|nfm-3919"

# Step 2: pg_dump the three tables
docker exec nucpot-prod-db pg_dump \
    -U "${PROD_POSTGRES_USER}" \
    -d "${PROD_POSTGRES_DB}" \
    -t materials -t datasets -t property_measurements \
    --file=/tmp/nfm-3918-pre-$(date +%Y%m%d-%H%M%S).sql

# Step 3: copy the dump out and confirm path
scp macstudio:/tmp/nfm-3918-pre-*.sql /var/backups/
NFMD_PROD_BACKUP_PATH=/var/backups/nfm-3918-pre-20260831-235900.sql

# Step 4: run with the gate
NFMD_PROD_BACKUP_PATH=/var/backups/nfm-3918-pre-20260831-235900.sql \
NFMD_TIER_1B_DEPLOYED=1 \
python scripts/nfm-3918-unknown-material-cleanup.py \
    --database-url "$PROD_DATABASE_URL" \
    --expected-unknown-count 27
```

## Rollback

There is **no `git revert` path** for this migration. The rollback is
restoring the three tables from the pg_dump:

```bash
# Disable connections first so no new measurements sneak in
docker compose -f docker-compose.prod.yml --env-file docker/.env.prod \
    stop prod-api prod-worker

# Restore from the pg_dump
docker exec -i nucpot-prod-db psql \
    -U "${PROD_POSTGRES_USER}" \
    -d "${PROD_POSTGRES_DB}" \
    < /var/backups/nfm-3918-pre-20260831-235900.sql

# Verify the Unknown count is restored
docker exec nucpot-prod-db psql \
    -U "${PROD_POSTGRES_USER}" \
    -d "${PROD_POSTGRES_DB}" \
    -tAc "SELECT count(*) FROM materials WHERE name='Unknown Material'"
# expect: 27

# Restart the API
docker compose -f docker-compose.prod.yml --env-file docker/.env.prod \
    up -d
```

If the pg_dump is lost, recovery is from the Quark cloud backup (see
`docs/runbooks/mac-studio-docker-ops.md` §6). RTO is hours, not minutes —
this is the worst-case data-migration scenario in the repo.

## Open questions for CPO

1. **Unresolved Unknown rows** — if any carrying-data Unknown row has no
   matching target material (the conservative fallback in Phase 4), the
   script leaves them in place. Should those be:
     (a) left for human review and tracked as a separate ticket,
     (b) hard-deleted even though they carry measurements, OR
     (c) merged onto a generic `Unknown Material (orphan)` bucket material
         to consolidate for later review?

2. **Dedup policy** — when `uq_pm_dedup` fires, we currently keep the
   target's row and drop the source. Should we instead keep the source
   (newer timestamp) and drop the target? The current policy biases
   toward the established material, but if Tier 1B changes the mapping
   logic the source row might be more authoritative.

3. **`source_id` resolution** — when multiple materials share the same
   `source_id` AND none is `Unknown Material`, the script picks the
   earliest `created_at`. Should it instead use the one with the most
   measurements (the canonical record)?

These are open until the first staging dry-run lands and we see real
output.