-- Migration: Add full-text search vector to potentials table
-- This was defined in schema.sql but never applied to the live database.
-- Fixes: browse page and search page keyword search returning 500 errors.

-- Enable extension if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Add the search_vector generated column
ALTER TABLE potentials ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', COALESCE(display_name, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(system_name, '')), 'A') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(tags, '{}'), ' ')), 'C') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(elements, '{}'), ' ')), 'A')
  ) STORED;

-- Create GIN index for full-text search
CREATE INDEX IF NOT EXISTS idx_potentials_search ON potentials USING GIN(search_vector);

-- Create trigram index for fuzzy name search (used by SearchSuggestions)
CREATE INDEX IF NOT EXISTS idx_potentials_name_trgm ON potentials USING GIN(display_name gin_trgm_ops);
