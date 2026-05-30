-- Add version field to potentials table
ALTER TABLE potentials
  ADD COLUMN IF NOT EXISTS version VARCHAR(16) NOT NULL DEFAULT '1.0';

-- Add comment
COMMENT ON COLUMN potentials.version IS 'Semantic version of the potential (e.g. 1.0, 2.1, 1.0.1)';
