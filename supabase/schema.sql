-- ============================================
-- NucPot: 核材料势函数库 数据库 Schema
-- MVP v0.1 | 2026-05-24
-- ============================================

-- 启用必要扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 势函数主表
CREATE TABLE potentials (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name            VARCHAR(128) NOT NULL UNIQUE,
  display_name    TEXT,
  type            VARCHAR(32) NOT NULL,     -- EAM, MEAM, ML, ReaxFF, Buckingham, other
  subtype         VARCHAR(64),              -- LAMMPS pair_style subtype
  format          VARCHAR(32),              -- setfl, eam.alloy, KIM, GULP, custom

  elements        TEXT[] NOT NULL,
  system_name     VARCHAR(128),
  system_tags     TEXT[],

  description     TEXT,
  applicability   JSONB DEFAULT '{}',       -- temperatureRange, pressureRange, phases, notes
  references      JSONB DEFAULT '[]',
  developers      JSONB DEFAULT '[]',

  verified_props  JSONB DEFAULT '{}',       -- latticeConstant, elasticConstants, etc.
  sim_software    TEXT[] DEFAULT '{}',
  lammps_config   JSONB DEFAULT '{}',       -- pair_style + pair_coeff commands

  file_url        TEXT,
  file_hash       VARCHAR(64),
  file_size       INTEGER,
  source          TEXT,                     -- NIST IPR, OpenKIM, developer direct

  license         VARCHAR(32) DEFAULT 'CC-BY-4.0',
  tags            TEXT[] DEFAULT '{}',
  extra           JSONB DEFAULT '{}',       -- irradiationRelevant, hasDefectData, etc.

  status          VARCHAR(16) DEFAULT 'published',
  created_by      VARCHAR(64) DEFAULT 'system',
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_potentials_type ON potentials(type);
CREATE INDEX idx_potentials_elements ON potentials USING GIN(elements);
CREATE INDEX idx_potentials_system_tags ON potentials USING GIN(system_tags);
CREATE INDEX idx_potentials_tags ON potentials USING GIN(tags);
CREATE INDEX idx_potentials_name_trgm ON potentials USING GIN(display_name gin_trgm_ops);
CREATE INDEX idx_potentials_status ON potentials(status);

-- 全文检索
ALTER TABLE potentials ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', COALESCE(display_name, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(system_name, '')), 'A') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(tags, '{}'), ' ')), 'C') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(elements, '{}'), ' ')), 'A')
  ) STORED;

CREATE INDEX idx_potentials_search ON potentials USING GIN(search_vector);

-- updated_at 自动更新触发器
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

-- ============================================
-- 种子数据：首批 10 个势函数
-- ============================================

INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, references, developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES

-- 1. U-Zr MEAM (Moore 2015)
('MEAM_UZr_Moore_2015',
 'MEAM Potential for U-Zr Alloy (Moore et al. 2015)',
 'MEAM', 'meam', 'DYNAMO',
 ARRAY['U', 'Zr'], 'U-Zr alloy',
 ARRAY['金属燃料', 'U-Zr合金'],
 'Semi-empirical MEAM potential for high temperature BCC γ-U-Zr alloy phase. First simulation to reproduce experimental thermodynamics of the γ-U-Zr metallic alloy system.',
 '{"temperatureRange": [300, 2500], "phases": ["BCC", "liquid"], "notes": "高温 γ-U-Zr 合金，适合金属燃料模拟"}',
 '[{"doi": "10.1016/j.jnucmat.2015.10.016", "citation": "Moore, Beeler, Deo, Baskes, Okuniewski, J. Nucl. Mater. 467, 802-819 (2015)"}]',
 '[{"name": "A.P. Moore", "affiliation": "Georgia Institute of Technology"}, {"name": "M.I. Baskes"}]',
 '{"meltingPoint": {"value": 1400, "unit": "K"}, "thermalExpansion": "verified", "heatCapacity": "verified", "mixingEnthalpy": "verified"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "meam", "pair_coeff": "* * library.meam U Zr UZr.meam U Zr", "note": "LAMMPS 需修改截断函数"}',
 'NIST IPR',
 ARRAY['核材料', '金属燃料', 'U-Zr', 'MEAM', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "basic"}'
),

-- 2. U MEAM (Fernandez 2014)
('MEAM_U_Fernandez_2014',
 'MEAM Potential for Uranium Metal (Fernández & Pascuet 2014)',
 'MEAM', 'meam', 'LAMMPS',
 ARRAY['U'], 'Pure uranium',
 ARRAY['金属燃料', '纯铀'],
 'MEAM potential for U metal. Reproduces lattice parameters and cohesive energy of orthorhombic αU. Predicts αU↔γU transformation and melting temperatures.',
 '{"temperatureRange": [300, 1500], "phases": ["α-U (orthorhombic)", "γ-U (BCC)", "liquid"], "notes": "纯铀全相态"}',
 '[{"doi": "10.1088/0965-0393/22/5/055019", "citation": "Fernández & Pascuet, Modell. Simul. Mater. Sci. Eng. 22(5), 055019 (2014)"}]',
 '[{"name": "J.R. Fernández", "affiliation": "CNEA Argentina"}, {"name": "M.I. Pascuet"}]',
 '{"latticeConstant": {"value": 2.85, "unit": "Å", "phase": "γ-U BCC"}, "meltingPoint": {"value": 1405, "unit": "K"}, "phaseTransition": {"value": 935, "unit": "K", "note": "αU → γU"}}',
 ARRAY['LAMMPS'],
 '{"pair_style": "meam", "pair_coeff": "* * library.meam U U.meam U"}',
 'NIST IPR',
 ARRAY['核材料', '金属燃料', 'U', 'MEAM', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": true, "hasLiquidPhase": true, "validationLevel": "benchmarked"}'
),

-- 3. UO2 Buckingham (Thompson 2014)
('Buckingham_UO2_Thompson_2014',
 'Buckingham/Shell Model Potential for UO₂ (Thompson et al. 2014)',
 'Buckingham', 'buckingham', 'GULP',
 ARRAY['U', 'O'], 'UO₂ (uranium dioxide)',
 ARRAY['氧化物燃料', 'UO2'],
 'Improved UO₂ interatomic potential fitted via Iterative Potential Refinement (IPR). Produces both accurate defect energetics and best agreement with experimental phonon dispersion.',
 '{"temperatureRange": [300, 3000], "phases": ["fluorite"], "notes": "高温缺陷环境适用"}',
 '[{"doi": "10.1016/j.jnucmat.2013.11.040", "citation": "Thompson, Meredig, Stan, Wolverton, J. Nucl. Mater. 446, 155-162 (2014)"}]',
 '[{"name": "A.E. Thompson"}]',
 '{"phononDispersion": "verified", "defectEnergetics": "verified", "phononDOS": "verified"}',
 ARRAY['GULP'],
 '{"note": "Shell model parameters for GULP"}',
 'NIST IPR',
 ARRAY['核材料', '氧化物燃料', 'UO2', 'Buckingham', 'GULP'],
 '{"irradiationRelevant": true, "hasDefectData": true, "hasLiquidPhase": false, "validationLevel": "benchmarked"}'
),

-- 4. UO2 Rigid Ion (Tiwary 2009)
('RigidIon_UO2_Tiwary_2009',
 'Rigid Ion Potential for UO₂ (Tiwary et al. 2009)',
 'other', 'zbl+coulomb', 'LAMMPS',
 ARRAY['U', 'O'], 'UO₂ (uranium dioxide)',
 ARRAY['氧化物燃料', 'UO2'],
 'Interatomic potentials for UO₂ valid from lattice vibrations to high-energy collisions. Based on charged-ion ZBL universal potential with ab initio intermediate range.',
 '{"temperatureRange": [300, 5000], "phases": ["fluorite", "liquid"], "notes": "全能量范围，含裂变级联碰撞"}',
 '[{"doi": "10.1103/physrevb.80.174302", "citation": "Tiwary, van de Walle, Grønbech-Jensen, Phys. Rev. B 80, 174302 (2009)"}]',
 '[{"name": "P. Tiwary"}, {"name": "A. van de Walle"}]',
 '{"note": "Validated across all energy scales"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "hybrid/overlay zbl 0.5 2.0 coul/long 10.0", "note": "ZBL + Coulomb hybrid"}',
 'NIST IPR',
 ARRAY['核材料', '氧化物燃料', 'UO2', 'ZBL', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": true, "hasLiquidPhase": true, "validationLevel": "basic"}'
),

-- 5. Zr RANN (Nitol 2022)
('RANN_Zr_Nitol_2022',
 'RANN Neural Network Potential for Zr (Nitol et al. 2022)',
 'ML', 'rann', 'LAMMPS',
 ARRAY['Zr'], 'Pure zirconium',
 ARRAY['包壳材料', '纯锆'],
 'Neural network potential based on RANN formalism with MEAM structural fingerprint. Accurately predicts α-β-ω phase transformations and equilibrium phase diagram.',
 '{"temperatureRange": [300, 2500], "phases": ["α-HCP", "β-BCC", "ω", "liquid"], "notes": "三相变预测"}',
 '[{"doi": "10.1016/j.actamat.2021.117347", "citation": "Nitol, Dickel, Barrett, Acta Mater. 224, 117347 (2022)"}]',
 '[{"name": "M.S. Nitol", "affiliation": "Mississippi State University"}]',
 '{"phaseTransformation": {"α→β": "1136K (exp: 1136K)", "triplePoint": "5.04 GPa, 988K"}, "phononSpectra": "verified"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "rann", "pair_coeff": "* * Zr_rann"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr', 'ML', 'RANN', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "benchmarked"}'
),

-- 6. Zr EAM (Mendelev 2007)
('EAM_Zr_Mendelev_2007',
 'EAM Potential for Zr (Mendelev & Ackland 2007)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Zr'], 'Pure zirconium',
 ARRAY['包壳材料', '纯锆'],
 'Classical EAM potential for Zr. Well-established baseline potential for zirconium simulations.',
 '{"temperatureRange": [300, 2500], "phases": ["HCP", "BCC", "liquid"]}',
 '[{"citation": "Mendelev & Ackland, Philos. Mag. Lett. (2007)"}]',
 '[{"name": "M.I. Mendelev", "affiliation": "Ames Laboratory"}]',
 '{}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * Zr_mendelev.eam.alloy Zr"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "basic"}'
),

-- 7. Zr-Nb EAM (Starikov 2021)
('EAM_ZrNb_Starikov_2021',
 'EAM Potential for Zr-Nb Alloy (Starikov & Smirnova 2021)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Zr', 'Nb'], 'Zr-Nb alloy',
 ARRAY['包壳材料', 'Zr-Nb合金'],
 'Optimized interatomic potential for Zr-Nb binary system, covering a wide range of component concentrations.',
 '{"temperatureRange": [300, 2000], "phases": ["HCP", "BCC"]}',
 '[{"doi": "10.1016/j.commatsci.2021.110581", "citation": "Starikov & Smirnova, Comput. Mater. Sci. 197, 110581 (2021)"}]',
 '[{"name": "S. Starikov"}, {"name": "D. Smirnova"}]',
 '{}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * ZrNb_starikov.eam.alloy Zr Nb"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr-Nb', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": false, "hasLiquidPhase": false, "validationLevel": "basic"}'
),

-- 8. Zr-Nb EAM (Fan 2024)
('EAM_ZrNb_Fan_2024',
 'EAM Potential for Zr-Nb with Nb Precipitates (Fan et al. 2024)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Zr', 'Nb'], 'Zr-Nb alloy',
 ARRAY['包壳材料', 'Zr-Nb合金'],
 'EAM potential for modeling bcc Nb precipitates in hcp Zr matrix. Accounts for coherency strain between phases.',
 '{"temperatureRange": [300, 1500], "phases": ["HCP Zr", "BCC Nb"], "notes": "Nb析出物模拟"}',
 '[{"doi": "10.1103/physrevmaterials.8.113601", "citation": "Fan, Maras, Cottura, Marinica, Clouet, Phys. Rev. Mater. 8, 113601 (2024)"}]',
 '[{"name": "Z. Fan"}]',
 '{"precipitateStructure": "verified", "coherencyStrain": "verified"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * ZrNb_fan.eam.alloy Zr Nb"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr-Nb', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": false, "hasLiquidPhase": false, "validationLevel": "basic"}'
),

-- 9. Fe EAM (Mendelev)
('EAM_Fe_Mendelev_2003',
 'EAM Potential for Fe (Mendelev et al.)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Fe'], 'Pure iron',
 ARRAY['结构材料', '纯铁'],
 'Well-established EAM potential for BCC iron. Baseline potential for structural steel simulations.',
 '{"temperatureRange": [300, 2000], "phases": ["BCC", "liquid"]}',
 '[{"citation": "Mendelev et al. (2003)"}]',
 '[{"name": "M.I. Mendelev", "affiliation": "Ames Laboratory"}]',
 '{}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * Fe_mendelev.eam.alloy Fe"}',
 'NIST IPR',
 ARRAY['结构材料', 'Fe', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "basic"}'
),

-- 10. Fe-Zr EAM
('EAM_FeZr',
 'EAM Potential for Fe-Zr System',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Fe', 'Zr'], 'Fe-Zr',
 ARRAY['结构材料', 'Fe-Zr'],
 'EAM potential for Fe-Zr interactions.',
 '{"temperatureRange": [300, 1500], "phases": ["BCC", "HCP"]}',
 '[]',
 '[]',
 '{}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * FeZr.eam.alloy Fe Zr"}',
 'NIST IPR',
 ARRAY['结构材料', 'Fe-Zr', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": false, "validationLevel": "basic"}'
);
