-- Phase 2: Auth profiles and contributions tables
-- Requires Supabase Auth to be enabled

-- 用户 profile 表
CREATE TABLE IF NOT EXISTS profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  username    VARCHAR(64) UNIQUE NOT NULL,
  full_name   VARCHAR(128),
  email       TEXT,
  role        VARCHAR(16) DEFAULT 'contributor',
  avatar_url  TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 贡献记录表
CREATE TABLE IF NOT EXISTS contributions (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id  UUID REFERENCES potentials(id) ON DELETE SET NULL,
  user_id       UUID REFERENCES profiles(id) ON DELETE SET NULL,
  action        VARCHAR(32) NOT NULL,
  status        VARCHAR(16) DEFAULT 'pending',
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: profiles
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Profiles viewable by all" ON profiles FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Users insert own profile" ON profiles FOR INSERT TO authenticated WITH CHECK (auth.uid() = id);
CREATE POLICY "Users update own profile" ON profiles FOR UPDATE TO authenticated USING (auth.uid() = id);

-- RLS: contributions
ALTER TABLE contributions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Contributions viewable by all" ON contributions FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Auth users create contributions" ON contributions FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Admin update contributions" ON contributions FOR UPDATE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);

-- RLS: potentials — allow authenticated users to create
CREATE POLICY "Auth users create potentials" ON potentials FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Admin update potentials" ON potentials FOR UPDATE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);
CREATE POLICY "Admin delete potentials" ON potentials FOR DELETE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_contributions_user ON contributions(user_id);
CREATE INDEX IF NOT EXISTS idx_contributions_potential ON contributions(potential_id);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON profiles(role);

-- Trigger for profiles updated_at
CREATE TRIGGER profiles_updated_at
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Grant schema + table permissions for anon/authenticated/service_role
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO authenticated;
