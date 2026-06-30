-- Migration: Add dedup_hash column to _ref_gap_fill_staging
-- Date: 2026-06-30
-- Issue: NFM-575 — discovered during E2E DOI extraction testing
--
-- The dedup_hash column was added to the SQLAlchemy model
-- (models/ref_gap_fill.py:99) but was never migrated to the
-- production database. This causes all v4 extraction jobs to
-- fail at the quality gate / staging step with:
--   UndefinedColumnError: column _ref_gap_fill_staging.dedup_hash does not exist

BEGIN;

-- Add dedup_hash column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'dedup_hash'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging
            ADD COLUMN dedup_hash VARCHAR(64) NOT NULL DEFAULT '';
    END IF;
END
$$;

-- Add index if not exists
CREATE INDEX IF NOT EXISTS idx_staging_dedup
    ON _ref_gap_fill_staging (dedup_hash);

-- Backfill dedup_hash for existing rows with NULL values (shouldn't exist,
-- but defensive). MD5 hash of element_system + phase + property_name + value
UPDATE _ref_gap_fill_staging
SET dedup_hash = encode(digest(
    element_system || '|' ||
    COALESCE(phase, '') || '|' ||
    property_name || '|' ||
    CAST(value AS TEXT) || '|' ||
    COALESCE(source, ''),
    'md5'
), 'hex')
WHERE dedup_hash = '';

COMMIT;
