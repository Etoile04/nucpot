-- Migration: Add quality-gate and v4 workflow columns to _ref_gap_fill_staging
-- Date: 2026-06-30
-- Issue: NFM-567 — discovered during E2E DOI extraction testing
--
-- The ORM model (models/ref_gap_fill.py) references dedup_hash, range_validated,
-- fill_batch_id, reviewer_id, reviewed_at, promoted_to_pm_id, promoted_at —
-- but the original table creation migration only had batch_id VARCHAR(100).
-- This causes v4 extraction jobs to fail at the staging step.
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

-- 3. Rename batch_id → fill_batch_id (VARCHAR → UUID)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'batch_id'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging RENAME COLUMN batch_id TO fill_batch_id;
        -- Clear non-UUID values before type change
        UPDATE _ref_gap_fill_staging SET fill_batch_id = NULL WHERE fill_batch_id::text !~ '^[';
        ALTER TABLE _ref_gap_fill_staging ALTER COLUMN fill_batch_id TYPE UUID
            USING CASE WHEN fill_batch_id IS NULL THEN NULL ELSE fill_batch_id::uuid END;
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'fill_batch_id'
    ) THEN
        ALTER TABLE _ref_gap_fill_staging ADD COLUMN fill_batch_id UUID;
    END IF;
END
$$;

-- 4. Review workflow columns
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_ref_gap_fill_staging'
        AND column_name = 'review_note'
    ) THEN
        -- Check if old name exists
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

-- 5. Indexes
CREATE INDEX IF NOT EXISTS idx_staging_fill_batch
    ON _ref_gap_fill_staging (fill_batch_id);

CREATE INDEX IF NOT EXISTS idx_staging_element_phase_prop
    ON _ref_gap_fill_staging (element_system, phase, property_name);

COMMIT;
