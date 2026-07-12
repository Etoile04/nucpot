-- Migration: Add missing dedup_hash and quality-gate columns (simplified)
-- Date: 2026-06-30
-- Issue: NFM-600 — production has old version of 001_add_dedup_hash.sql
--   that uses encode(digest()) requiring pgcrypto extension.
--   This file is a self-contained, no-extension-needed alternative.
-- Idempotent: safe to re-run.

BEGIN;

-- 1. dedup_hash (critical: unblocks extraction pipeline staging)
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

CREATE INDEX IF NOT EXISTS idx_staging_dedup
    ON _ref_gap_fill_staging (dedup_hash);

-- 2. range_validated
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'range_validated'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging
            ADD COLUMN range_validated BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;
END
$$;

-- 3. fill_batch_id (UUID) — only if not already present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'fill_batch_id'
    ) THEN
        -- If old batch_id exists, rename it
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = '_ref_gap_fill_staging'
            AND column_name = 'batch_id'
        ) THEN
            ALTER TABLE _ref_gap_fill_staging RENAME COLUMN batch_id TO fill_batch_id;
            UPDATE _ref_gap_fill_staging SET fill_batch_id = NULL
                WHERE fill_batch_id::text !~ '^[';
            ALTER TABLE _ref_gap_fill_staging ALTER COLUMN fill_batch_id TYPE UUID
                USING CASE WHEN fill_batch_id IS NULL THEN NULL
                    ELSE fill_batch_id::uuid END;
        ELSE
            ALTER TABLE _ref_gap_fill_staging ADD COLUMN fill_batch_id UUID;
        END IF;
    END IF;
END
$$;

-- 4. review_note (handle old review_notes name)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'review_note'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = '_ref_gap_fill_staging'
            AND column_name = 'review_notes'
        ) THEN
            ALTER TABLE _ref_gap_fill_staging RENAME COLUMN review_notes TO review_note;
        ELSE
            ALTER TABLE _ref_gap_fill_staging ADD COLUMN review_note TEXT;
        END IF;
    END IF;
END
$$;

-- 5. reviewer_id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'reviewer_id'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging ADD COLUMN reviewer_id UUID;
    END IF;
END
$$;

-- 6. reviewed_at
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'reviewed_at'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging ADD COLUMN reviewed_at TIMESTAMPTZ;
    END IF;
END
$$;

-- 7. promoted_to_pm_id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'promoted_to_pm_id'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging ADD COLUMN promoted_to_pm_id UUID;
    END IF;
END
$$;

-- 8. promoted_at
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'promoted_at'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging ADD COLUMN promoted_at TIMESTAMPTZ;
    END IF;
END
$$;

-- 9. Indexes
CREATE INDEX IF NOT EXISTS idx_staging_fill_batch
    ON _ref_gap_fill_staging (fill_batch_id);

CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop
    ON _ref_gap_fill_staging (element_system, phase, property_name);

COMMIT;
