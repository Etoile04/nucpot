-- Feedback table for user feedback collection
CREATE TABLE IF NOT EXISTS feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id),
  type VARCHAR(32) NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  email VARCHAR(255),
  status VARCHAR(16) DEFAULT 'open',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;

-- Anyone (including anonymous) can submit feedback
CREATE POLICY "Anyone can insert feedback" ON feedback
  FOR INSERT WITH CHECK (true);

-- Authenticated users can read their own feedback, admins can read all
CREATE POLICY "Users can read own feedback" ON feedback
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Admins can read all feedback" ON feedback
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
  );
