-- 001_priority_score.sql
-- NFM-3576: Add priority_score column to extraction_candidates
--
-- Forward-compatible: nullable, default NULL, no NOT NULL constraint.
-- Safe to apply before the Python backfill populates any rows.
-- Idempotent: uses IF NOT EXISTS / IF EXISTS guards throughout.

BEGIN;

-- Column addition (idempotent via DO block)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'extraction_candidates'
          AND column_name = 'priority_score'
    ) THEN
        ALTER TABLE extraction_candidates
            ADD COLUMN priority_score NUMERIC(6,4) DEFAULT NULL;
    END IF;
END $$;

-- Index for ranking query (idempotent via IF NOT EXISTS)
CREATE INDEX IF NOT EXISTS
    ix_extraction_candidates_priority_score_desc
    ON extraction_candidates (priority_score DESC, candidate_id);

COMMIT;

-- ============================================================
-- ROLLBACK (run manually if needed — also idempotent)
-- ============================================================
-- BEGIN;
-- DROP INDEX IF EXISTS ix_extraction_candidates_priority_score_desc;
-- ALTER TABLE extraction_candidates DROP COLUMN IF EXISTS priority_score;
-- COMMIT;