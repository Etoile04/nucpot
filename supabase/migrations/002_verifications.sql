-- 验证任务表
CREATE TABLE IF NOT EXISTS verifications (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,
  template VARCHAR(32) NOT NULL DEFAULT 'basic',
  status VARCHAR(16) DEFAULT 'pending',
  progress REAL DEFAULT 0,
  current_step TEXT,
  estimated_remaining_seconds INTEGER,
  results JSONB DEFAULT '[]',
  overall_grade VARCHAR(2),
  summary TEXT,
  error_log TEXT,
  triggered_by VARCHAR(64),
  compute_time_seconds INTEGER,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_verifications_potential ON verifications(potential_id);
CREATE INDEX idx_verifications_status ON verifications(status);
CREATE INDEX idx_verifications_created ON verifications(created_at DESC);

-- 启用 RLS
ALTER TABLE verifications ENABLE ROW LEVEL SECURITY;

-- 匿名用户可读
CREATE POLICY "Verifications are publicly readable"
  ON verifications FOR SELECT
  USING (true);

-- service_role 可写
CREATE POLICY "Service role can insert verifications"
  ON verifications FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Service role can update verifications"
  ON verifications FOR UPDATE
  USING (true);
