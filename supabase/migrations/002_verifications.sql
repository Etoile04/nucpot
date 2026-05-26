-- ============================================
-- NucPot: 验证管线数据库迁移
-- Phase 2 MVP | 2026-05-27
-- ============================================

-- 验证任务表
CREATE TABLE IF NOT EXISTS verifications (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id  UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,
  status        VARCHAR(16) DEFAULT 'pending',  -- pending/running/completed/failed
  requested_by  VARCHAR(64),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  completed_at  TIMESTAMPTZ,
  
  -- 计算结果
  results       JSONB DEFAULT '{}',
  -- { lattice_constant: {value: 3.42, unit: "Å", reference: 3.38, error_pct: 1.18, grade: "A"}, ... }
  
  overall_grade VARCHAR(2),      -- A/B/C/D/F
  summary       TEXT,            -- 人类可读摘要
  error_log     TEXT,            -- 错误日志（失败时）
  compute_time  INTEGER          -- 计算耗时（秒）
);

-- 参考值表（DFT/实验值）
CREATE TABLE IF NOT EXISTS reference_values (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  element_system VARCHAR(64) NOT NULL,  -- "U", "U-Mo", "U-Zr", "Zr", "Nb", "Mo"
  phase         VARCHAR(32),            -- "BCC", "FCC", "gamma", "alpha"
  property      VARCHAR(64) NOT NULL,   -- "lattice_constant", "C11", "C12", "C44", "vacancy_formation_energy"
  value         DOUBLE PRECISION NOT NULL,
  unit          VARCHAR(16),
  uncertainty   DOUBLE PRECISION,
  temperature   DOUBLE PRECISION,       -- K
  pressure      DOUBLE PRECISION DEFAULT 0,
  source        TEXT,                    -- 论文引用
  source_doi    VARCHAR(128),
  method        VARCHAR(32),            -- "experiment", "DFT", "calculated"
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_verifications_potential ON verifications(potential_id);
CREATE INDEX IF NOT EXISTS idx_verifications_status ON verifications(status);
CREATE INDEX IF NOT EXISTS idx_ref_element ON reference_values(element_system);
CREATE INDEX IF NOT EXISTS idx_ref_property ON reference_values(property);
CREATE INDEX IF NOT EXISTS idx_ref_element_property ON reference_values(element_system, property);

-- ============================================
-- 种子数据：核材料参考值
-- ============================================

INSERT INTO reference_values (element_system, phase, property, value, unit, temperature, source, source_doi, method) VALUES

-- === 铀 (U) ===
-- γ-U (BCC, 高温相, 室温外推值)
('U', 'BCC', 'lattice_constant', 3.47, 'Å', 300, 'Fernández & Pascuet 2014, Modell. Simul. Mater. Sci. Eng.', '10.1088/0965-0393/22/5/055019', 'DFT'),
('U', 'BCC', 'C11', 119.0, 'GPa', 0, 'Smirnov 2019, J. Nucl. Mater.', NULL, 'DFT'),
('U', 'BCC', 'C12', 103.0, 'GPa', 0, 'Smirnov 2019, J. Nucl. Mater.', NULL, 'DFT'),
('U', 'BCC', 'C44', 76.0, 'GPa', 0, 'Smirnov 2019, J. Nucl. Mater.', NULL, 'DFT'),
('U', 'BCC', 'vacancy_formation_energy', 1.88, 'eV', 0, 'Beeler 2013, J. Nucl. Mater.', '10.1016/j.jnucmat.2013.06.027', 'DFT'),

-- === 钼 (Mo) ===
('Mo', 'BCC', 'lattice_constant', 3.147, 'Å', 300, 'Experiment (ASM Handbook)', NULL, 'experiment'),
('Mo', 'BCC', 'C11', 463.0, 'GPa', 300, 'Experiment (Landolt-Börnstein)', NULL, 'experiment'),
('Mo', 'BCC', 'C12', 161.0, 'GPa', 300, 'Experiment (Landolt-Börnstein)', NULL, 'experiment'),
('Mo', 'BCC', 'C44', 109.0, 'GPa', 300, 'Experiment (Landolt-Börnstein)', NULL, 'experiment'),
('Mo', 'BCC', 'vacancy_formation_energy', 3.0, 'eV', 0, 'Ma et al. 2018, J. Phys.: Condens. Matter', NULL, 'DFT'),

-- === 锆 (Zr) ===
('Zr', 'BCC', 'lattice_constant', 3.61, 'Å', 1136, 'Experiment (high-T β-Zr)', NULL, 'experiment'),
('Zr', 'HCP', 'lattice_constant', 3.232, 'Å', 300, 'Experiment (ASM Handbook)', NULL, 'experiment'),
('Zr', 'HCP', 'C11', 143.0, 'GPa', 300, 'Experiment', NULL, 'experiment'),
('Zr', 'HCP', 'C33', 165.0, 'GPa', 300, 'Experiment', NULL, 'experiment'),
('Zr', 'BCC', 'vacancy_formation_energy', 2.07, 'eV', 0, 'Mendelev & Ackland 2007', NULL, 'calculated'),

-- === 铀-钼合金 (U-Mo) ===
('U-Mo', 'BCC', 'lattice_constant', 3.40, 'Å', 300, 'Kim et al. 2017, J. Nucl. Mater. (U-10Mo approx)', NULL, 'experiment'),
('U-Mo', 'BCC', 'C11', 135.0, 'GPa', 0, 'Hu et al. 2019, Calphad (U-10Mo)', NULL, 'DFT'),
('U-Mo', 'BCC', 'C12', 108.0, 'GPa', 0, 'Hu et al. 2019, Calphad (U-10Mo)', NULL, 'DFT'),

-- === 铌 (Nb) ===
('Nb', 'BCC', 'lattice_constant', 3.3008, 'Å', 300, 'Experiment (ASM Handbook)', NULL, 'experiment'),
('Nb', 'BCC', 'C11', 246.0, 'GPa', 300, 'Experiment', NULL, 'experiment'),
('Nb', 'BCC', 'C12', 134.0, 'GPa', 300, 'Experiment', NULL, 'experiment'),
('Nb', 'BCC', 'C44', 28.0, 'GPa', 300, 'Experiment', NULL, 'experiment'),
('Nb', 'BCC', 'vacancy_formation_energy', 2.65, 'eV', 0, 'DFT (VASP)', NULL, 'DFT');

