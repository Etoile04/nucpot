-- ============================================
-- NucPot: Verification Pipeline Schema
-- Migration 002 | 2026-05-27
-- ============================================

-- 验证任务表
CREATE TABLE verifications (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id    UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,

  status          VARCHAR(16) DEFAULT 'pending'
                  CHECK (status IN ('pending','running','completed','failed')),

  properties_requested JSONB NOT NULL DEFAULT '[]',
  -- e.g. ["lattice_constant", "elastic_constants", "vacancy_formation_energy"]

  results         JSONB DEFAULT '{}',
  -- e.g. {"lattice_constant": {"computed": 3.45, "reference": 3.47, "grade": "A", ...}, ...}

  overall_grade   VARCHAR(2),
  -- A / B / C / D / F

  error_message   TEXT,

  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW(),

  created_by      VARCHAR(64) DEFAULT 'system'
);

CREATE INDEX idx_verifications_potential ON verifications(potential_id);
CREATE INDEX idx_verifications_status ON verifications(status);
CREATE INDEX idx_verifications_created ON verifications(created_at DESC);

-- 参考值表（实验/DFT 基准数据）
CREATE TABLE reference_values (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  material        VARCHAR(32) NOT NULL,    -- U, Mo, Zr, U-Mo, U-Zr, Fe, Nb, etc.
  structure       VARCHAR(16) NOT NULL,    -- BCC, FCC, HCP, etc.

  property_name   VARCHAR(64) NOT NULL,    -- lattice_constant, C11, C12, C44, cohesive_energy, etc.
  value           DOUBLE PRECISION NOT NULL,
  unit            VARCHAR(16) NOT NULL,    -- angstrom, GPa, eV, eV/atom

  source          VARCHAR(128),            -- 文献引用
  temperature     DOUBLE PRECISION DEFAULT 300,  -- K
  notes           TEXT,

  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ref_material ON reference_values(material);
CREATE INDEX idx_ref_material_structure ON reference_values(material, structure);
CREATE UNIQUE INDEX idx_ref_unique ON reference_values(material, structure, property_name);

-- ============================================
-- 种子数据：核材料参考值
-- ============================================

-- Uranium (γ-U BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('U', 'BCC', 'lattice_constant', 3.47, 'angstrom', 'Smirnov2014', 'γ-U at high T'),
('U', 'BCC', 'C11', 74.0, 'GPa', 'Smirnov2014', NULL),
('U', 'BCC', 'C12', 51.0, 'GPa', 'Smirnov2014', NULL),
('U', 'BCC', 'C44', 73.0, 'GPa', 'Smirnov2014', NULL),
('U', 'BCC', 'cohesive_energy', 5.49, 'eV/atom', 'Smirnov2014', 'absolute value'),
('U', 'BCC', 'bulk_modulus', 58.7, 'GPa', 'calculated', '(C11+2*C12)/3');

-- Uranium (α-U orthorhombic) — partial
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('U', 'orthorhombic', 'lattice_a', 2.854, 'angstrom', 'experiment', 'α-U room T'),
('U', 'orthorhombic', 'lattice_b', 5.870, 'angstrom', 'experiment', NULL),
('U', 'orthorhombic', 'lattice_c', 4.955, 'angstrom', 'experiment', NULL);

-- Molybdenum (BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Mo', 'BCC', 'lattice_constant', 3.147, 'angstrom', 'experiment', NULL),
('Mo', 'BCC', 'C11', 463.0, 'GPa', 'experiment', NULL),
('Mo', 'BCC', 'C12', 161.0, 'GPa', 'experiment', NULL),
('Mo', 'BCC', 'C44', 109.0, 'GPa', 'experiment', NULL),
('Mo', 'BCC', 'cohesive_energy', 6.82, 'eV/atom', 'experiment', 'absolute value'),
('Mo', 'BCC', 'bulk_modulus', 261.7, 'GPa', 'calculated', '(C11+2*C12)/3');

-- Zirconium (β-Zr BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Zr', 'BCC', 'lattice_constant', 3.609, 'angstrom', 'experiment', 'β-Zr >1136K'),
('Zr', 'BCC', 'cohesive_energy', 6.25, 'eV/atom', 'experiment', 'absolute value');

-- Zirconium (α-Zr HCP)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Zr', 'HCP', 'lattice_a', 3.232, 'angstrom', 'experiment', 'α-Zr room T'),
('Zr', 'HCP', 'lattice_c', 5.147, 'angstrom', 'experiment', NULL),
('Zr', 'HCP', 'C11', 143.0, 'GPa', 'experiment', NULL),
('Zr', 'HCP', 'C12', 72.0, 'GPa', 'experiment', NULL),
('Zr', 'HCP', 'C44', 32.0, 'GPa', 'experiment', NULL);

-- U-Mo alloy (γ-U-Mo BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('U-Mo', 'BCC', 'lattice_constant', 3.39, 'angstrom', 'Smirnov2014', 'U-10Mo at high T'),
('U-Mo', 'BCC', 'C11', 140.0, 'GPa', 'Smirnov2014', NULL),
('U-Mo', 'BCC', 'cohesive_energy', 5.80, 'eV/atom', 'estimated', 'approximate');

-- U-Zr alloy (γ-U-Zr BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('U-Zr', 'BCC', 'lattice_constant', 3.52, 'angstrom', 'Landa2002', 'γ-U-Zr alloy'),
('U-Zr', 'BCC', 'cohesive_energy', 5.60, 'eV/atom', 'estimated', 'approximate');

-- Iron (BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Fe', 'BCC', 'lattice_constant', 2.870, 'angstrom', 'experiment', 'α-Fe room T'),
('Fe', 'BCC', 'C11', 230.0, 'GPa', 'experiment', NULL),
('Fe', 'BCC', 'C12', 135.0, 'GPa', 'experiment', NULL),
('Fe', 'BCC', 'C44', 116.0, 'GPa', 'experiment', NULL),
('Fe', 'BCC', 'cohesive_energy', 4.28, 'eV/atom', 'experiment', 'absolute value'),
('Fe', 'BCC', 'bulk_modulus', 166.7, 'GPa', 'calculated', '(C11+2*C12)/3');

-- Niobium (BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Nb', 'BCC', 'lattice_constant', 3.300, 'angstrom', 'experiment', NULL),
('Nb', 'BCC', 'C11', 246.0, 'GPa', 'experiment', NULL),
('Nb', 'BCC', 'C12', 134.0, 'GPa', 'experiment', NULL),
('Nb', 'BCC', 'C44', 28.0, 'GPa', 'experiment', NULL),
('Nb', 'BCC', 'cohesive_energy', 7.57, 'eV/atom', 'experiment', 'absolute value');

-- Zr-Nb alloy (HCP/BCC)
INSERT INTO reference_values (material, structure, property_name, value, unit, source, notes) VALUES
('Zr-Nb', 'HCP', 'lattice_a', 3.23, 'angstrom', 'experiment', 'Zr-2.5Nb'),
('Zr-Nb', 'HCP', 'lattice_c', 5.15, 'angstrom', 'experiment', NULL);
