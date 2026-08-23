# Priority Score Backfill Runbook

> **Issue:** NFM-3576 | **Parent:** NFM-3548 (Phase 5.3 priority scoring refactor)
> **Last updated:** 2026-08-23

## 1. Prerequisites

| Item | Value |
|------|-------|
| `DATABASE_URL` | Postgres connection string (must have DDL+DML on `extraction_candidates`) |
| `NFM_PRIORITY_V2_ENABLED` | `true` (enables the new scoring formula in the Python layer) |
| Python env | `nucpot` venv with `nfm_db` package installed |
| Access | Direct DB access + application-layer backfill script |

## 2. Apply the Schema Migration

```bash
psql "$DATABASE_URL" -f pr/migrations/001_priority_score.sql
```

Expected output: `BEGIN` / `DO` / `CREATE INDEX` / `COMMIT`.  
Re-running produces zero diffs (all guards are idempotent).

## 3. Run the Backfill

```bash
export DATABASE_URL="<your-connection-string>"
export NFM_PRIORITY_V2_ENABLED=true

python -m nfm_db.scripts.backfill_priority_score \
    --batch-size 5000 \
    --dry-run          # remove after first verification
```

### Expected Metrics

| Metric | Value |
|--------|-------|
| Estimated row count | ~50 000 candidates (check with `SELECT count(*) FROM extraction_candidates`) |
| Wall-clock time | 3–8 min (depends on DB latency and LLM call overhead) |
| Writes | One `UPDATE` per row where `priority_score IS NULL` |

## 4. Idempotency Guarantee

The backfill script **only updates rows where `priority_score IS NULL`**:

```sql
UPDATE extraction_candidates
SET priority_score = $1
WHERE candidate_id = $2
  AND priority_score IS NULL;
```

A second run on already-populated data produces **zero writes** and **zero diffs**.  
After weight-tuning, force a full re-rank by setting scores back to NULL:

```sql
UPDATE extraction_candidates SET priority_score = NULL;
```

Then re-run the backfill.

## 5. Verify

```sql
-- Total candidates
SELECT count(*) AS total FROM extraction_candidates;

-- Populated vs null
SELECT
    count(*) FILTER (WHERE priority_score IS NOT NULL) AS scored,
    count(*) FILTER (WHERE priority_score IS NULL)    AS pending
FROM extraction_candidates;

-- Score distribution
SELECT
    count(*)              AS cnt,
    min(priority_score)   AS lo,
    max(priority_score)   AS hi,
    round(avg(priority_score)::numeric, 4) AS avg
FROM extraction_candidates
WHERE priority_score IS NOT NULL;
```

## 6. Rollback (Idempotent)

```sql
BEGIN;
DROP INDEX IF EXISTS ix_extraction_candidates_priority_score_desc;
ALTER TABLE extraction_candidates DROP COLUMN IF EXISTS priority_score;
COMMIT;
```

Re-running rollback is safe — `IF EXISTS` guards prevent errors.

## 7. Forward + Rollback + Forward Test (Dev)

```bash
# 1. Forward
psql "$DATABASE_URL" -f pr/migrations/001_priority_score.sql

# 2. Verify column + index exist
psql "$DATABASE_URL" -c "
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name='extraction_candidates' AND column_name='priority_score';
"

# 3. Rollback
psql "$DATABASE_URL" -c "
    BEGIN;
    DROP INDEX IF EXISTS ix_extraction_candidates_priority_score_desc;
    ALTER TABLE extraction_candidates DROP COLUMN IF EXISTS priority_score;
    COMMIT;
"

# 4. Forward again (proves idempotent re-apply)
psql "$DATABASE_URL" -f pr/migrations/001_priority_score.sql

# 5. Confirm clean state
psql "$DATABASE_URL" -c "
    SELECT indexname FROM pg_indexes
    WHERE tablename='extraction_candidates'
      AND indexname='ix_extraction_candidates_priority_score_desc';
"
```

All five steps must complete without errors for AC sign-off.
