-- ============================================
-- NucPot: 基础 potentials 表 (MVP 简化版)
-- Phase 2 MVP | 2026-05-27
-- 独立于 Supabase Auth，用于本地开发
-- ============================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS potentials (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name        VARCHAR(256) NOT NULL UNIQUE,
  type        VARCHAR(64) NOT NULL,    -- "EAM", "MEAM", "2NN-MEAM", "LJ", "ACE"
  elements    TEXT[] NOT NULL,          -- ["U"], ["U", "Mo"], ["Zr"]
  description TEXT,
  file_url    TEXT,                     -- 势函数文件路径或 URL
  lammps_config JSONB DEFAULT '{}',    -- LAMMPS calculator 配置
  year        INTEGER,
  source      TEXT,                     -- 论文引用
  source_doi  VARCHAR(128),
  submitted_by VARCHAR(64),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 触发器：自动更新 updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER potentials_updated_at
  BEFORE UPDATE ON potentials
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 种子数据：示例势函数
INSERT INTO potentials (name, type, elements, description, file_url, lammps_config, source) VALUES
('EAM_U_Zhou_2004', 'EAM', ARRAY['U'], 'EAM potential for U by Zhou (2004)', NULL,
 '{"pair_style": "eam/alloy", "pair_coeff": ["* * U.eam.alloy U"]}',
 'Zhou et al. 2004'),
('EAM_Mo_Ackland1', 'EAM', ARRAY['Mo'], 'EAM potential for Mo (Ackland–Thompson)', NULL,
 '{"pair_style": "eam/alloy", "pair_coeff": ["* * Mo.eam.alloy Mo"]}',
 'Ackland & Thompson 2004'),
('EAM_Zr_Mendelev_2007', 'EAM', ARRAY['Zr'], 'EAM potential for Zr (Mendelev–Ackland)', NULL,
 '{"pair_style": "eam/alloy", "pair_coeff": ["* * Zr.eam.alloy Zr"]}',
 'Mendelev & Ackland 2007'),
('EAM_Nb_Mendelev_2012', 'EAM', ARRAY['Nb'], 'EAM potential for Nb', NULL,
 '{"pair_style": "eam/alloy", "pair_coeff": ["* * Nb.eam.alloy Nb"]}',
 'Mendelev et al. 2012'),
('EAM_UMo_Xiang_2021', 'EAM', ARRAY['U', 'Mo'], 'EAM potential for U-Mo alloy (Xiang et al.)', NULL,
 '{"pair_style": "eam/alloy", "pair_coeff": ["* * UMo.eam.alloy U Mo"]}',
 'Xiang et al. 2021');
