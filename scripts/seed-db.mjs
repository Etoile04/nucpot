// 一次性脚本：在本地 Supabase 上创建 potentials 表 + 种子数据
import { createClient } from '@supabase/supabase-js'
import pg from 'pg'
import { readFileSync } from 'fs'

const connectionString = 'postgresql://postgres:postgres@127.0.0.1:54322/postgres'

async function main() {
  const client = new pg.Client({ connectionString })
  await client.connect()
  console.log('Connected to Supabase PostgreSQL')

  // 读取 schema.sql（去掉 Supabase 客户端不兼容的全文检索部分，用纯 SQL）
  const schema = readFileSync('./supabase/schema.sql', 'utf8')
  
  // 逐条执行
  try {
    await client.query('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    console.log('✅ uuid-ossp extension ready')
    
    await client.query('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    console.log('✅ pg_trgm extension ready')

    // Drop if exists (idempotent)
    await client.query('DROP TABLE IF EXISTS potentials CASCADE')
    console.log('✅ Dropped existing potentials table')

    // Create table
    await client.query(`
      CREATE TABLE potentials (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name            VARCHAR(128) NOT NULL UNIQUE,
        display_name    TEXT,
        type            VARCHAR(32) NOT NULL,
        subtype         VARCHAR(64),
        format          VARCHAR(32),
        elements        TEXT[] NOT NULL,
        system_name     VARCHAR(128),
        system_tags     TEXT[],
        description     TEXT,
        applicability   JSONB DEFAULT '{}',
        "references"    JSONB DEFAULT '[]',
        developers      JSONB DEFAULT '[]',
        verified_props  JSONB DEFAULT '{}',
        sim_software    TEXT[] DEFAULT '{}',
        lammps_config   JSONB DEFAULT '{}',
        file_url        TEXT,
        file_hash       VARCHAR(64),
        file_size       INTEGER,
        source          TEXT,
        license         VARCHAR(32) DEFAULT 'CC-BY-4.0',
        tags            TEXT[] DEFAULT '{}',
        extra           JSONB DEFAULT '{}',
        status          VARCHAR(16) DEFAULT 'published',
        created_by      VARCHAR(64) DEFAULT 'system',
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
      )
    `)
    console.log('✅ Created potentials table')

    // Indexes
    await client.query('CREATE INDEX idx_potentials_type ON potentials(type)')
    await client.query('CREATE INDEX idx_potentials_elements ON potentials USING GIN(elements)')
    await client.query('CREATE INDEX idx_potentials_system_tags ON potentials USING GIN(system_tags)')
    await client.query('CREATE INDEX idx_potentials_tags ON potentials USING GIN(tags)')
    await client.query("CREATE INDEX idx_potentials_name_trgm ON potentials USING GIN(display_name gin_trgm_ops)")
    await client.query('CREATE INDEX idx_potentials_status ON potentials(status)')
    console.log('✅ Created indexes')

    // Trigger
    await client.query(`
      CREATE OR REPLACE FUNCTION update_updated_at()
      RETURNS TRIGGER AS $$
      BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql
    `)
    await client.query(`
      DROP TRIGGER IF EXISTS potentials_updated_at ON potentials;
      CREATE TRIGGER potentials_updated_at
        BEFORE UPDATE ON potentials
        FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    `)
    console.log('✅ Created updated_at trigger')

    // Full-text search - use trigger instead of generated column (array_to_string is not immutable)
    await client.query(`
      ALTER TABLE potentials ADD COLUMN IF NOT EXISTS search_vector tsvector
    `)
    await client.query(`
      CREATE OR REPLACE FUNCTION update_search_vector() RETURNS TRIGGER AS $$
      BEGIN
        NEW.search_vector :=
          setweight(to_tsvector('english', COALESCE(NEW.display_name, '')), 'A') ||
          setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
          setweight(to_tsvector('english', COALESCE(NEW.system_name, '')), 'A');
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql
    `)
    await client.query(`
      DROP TRIGGER IF EXISTS potentials_search_vector ON potentials;
      CREATE TRIGGER potentials_search_vector
        BEFORE INSERT OR UPDATE ON potentials
        FOR EACH ROW EXECUTE FUNCTION update_search_vector()
    `)
    // Update search vectors for existing rows
    await client.query('UPDATE potentials SET search_vector = to_tsvector(\'english\', COALESCE(display_name, \'\') || \'  \' || COALESCE(description, \'\') || \'  \' || COALESCE(system_name, \'\'))')
    console.log('✅ Updated search vectors')
    console.log('✅ Created full-text search')

  } catch (err) {
    console.error('Schema error:', err.message)
    process.exit(1)
  }

  // Seed data - use the INSERT statements from schema.sql
  const seeds = [
    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
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
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
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
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
('Buckingham_UO2_Thompson_2014',
 'Buckingham/Shell Model Potential for UO₂ (Thompson et al. 2014)',
 'Buckingham', 'buckingham', 'GULP',
 ARRAY['U', 'O'], 'UO₂ (uranium dioxide)',
 ARRAY['氧化物燃料', 'UO2'],
 'Improved UO₂ interatomic potential fitted via Iterative Potential Refinement (IPR).',
 '{"temperatureRange": [300, 3000], "phases": ["fluorite"], "notes": "高温缺陷环境适用"}',
 '[{"doi": "10.1016/j.jnucmat.2013.11.040", "citation": "Thompson, Meredig, Stan, Wolverton, J. Nucl. Mater. 446, 155-162 (2014)"}]',
 '[{"name": "A.E. Thompson"}]',
 '{"phononDispersion": "verified", "defectEnergetics": "verified", "phononDOS": "verified"}',
 ARRAY['GULP'],
 '{"note": "Shell model parameters for GULP"}',
 'NIST IPR',
 ARRAY['核材料', '氧化物燃料', 'UO2', 'Buckingham', 'GULP'],
 '{"irradiationRelevant": true, "hasDefectData": true, "hasLiquidPhase": false, "validationLevel": "benchmarked"}'
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
('RigidIon_UO2_Tiwary_2009',
 'Rigid Ion Potential for UO₂ (Tiwary et al. 2009)',
 'other', 'zbl+coulomb', 'LAMMPS',
 ARRAY['U', 'O'], 'UO₂ (uranium dioxide)',
 ARRAY['氧化物燃料', 'UO2'],
 'Interatomic potentials for UO₂ valid from lattice vibrations to high-energy collisions.',
 '{"temperatureRange": [300, 5000], "phases": ["fluorite", "liquid"], "notes": "全能量范围，含裂变级联碰撞"}',
 '[{"doi": "10.1103/physrevb.80.174302", "citation": "Tiwary, van de Walle, Grønbech-Jensen, Phys. Rev. B 80, 174302 (2009)"}]',
 '[{"name": "P. Tiwary"}, {"name": "A. van de Walle"}]',
 '{"note": "Validated across all energy scales"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "hybrid/overlay zbl 0.5 2.0 coul/long 10.0", "note": "ZBL + Coulomb hybrid"}',
 'NIST IPR',
 ARRAY['核材料', '氧化物燃料', 'UO2', 'ZBL', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": true, "hasLiquidPhase": true, "validationLevel": "basic"}'
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
('RANN_Zr_Nitol_2022',
 'RANN Neural Network Potential for Zr (Nitol et al. 2022)',
 'ML', 'rann', 'LAMMPS',
 ARRAY['Zr'], 'Pure zirconium',
 ARRAY['包壳材料', '纯锆'],
 'Neural network potential based on RANN formalism with MEAM structural fingerprint.',
 '{"temperatureRange": [300, 2500], "phases": ["α-HCP", "β-BCC", "ω", "liquid"], "notes": "三相变预测"}',
 '[{"doi": "10.1016/j.actamat.2021.117347", "citation": "Nitol, Dickel, Barrett, Acta Mater. 224, 117347 (2022)"}]',
 '[{"name": "M.S. Nitol", "affiliation": "Mississippi State University"}]',
 '{"phaseTransformation": {"α→β": "1136K (exp: 1136K)", "triplePoint": "5.04 GPa, 988K"}, "phononSpectra": "verified"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "rann", "pair_coeff": "* * Zr_rann"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr', 'ML', 'RANN', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "benchmarked"}'
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
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
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
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
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
('EAM_ZrNb_Fan_2024',
 'EAM Potential for Zr-Nb with Nb Precipitates (Fan et al. 2024)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Zr', 'Nb'], 'Zr-Nb alloy',
 ARRAY['包壳材料', 'Zr-Nb合金'],
 'EAM potential for modeling bcc Nb precipitates in hcp Zr matrix.',
 '{"temperatureRange": [300, 1500], "phases": ["HCP Zr", "BCC Nb"], "notes": "Nb析出物模拟"}',
 '[{"doi": "10.1103/physrevmaterials.8.113601", "citation": "Fan, Maras, Cottura, Marinica, Clouet, Phys. Rev. Mater. 8, 113601 (2024)"}]',
 '[{"name": "Z. Fan"}]',
 '{"precipitateStructure": "verified", "coherencyStrain": "verified"}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * ZrNb_fan.eam.alloy Zr Nb"}',
 'NIST IPR',
 ARRAY['核材料', '包壳材料', 'Zr-Nb', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": true, "hasDefectData": false, "hasLiquidPhase": false, "validationLevel": "basic"}'
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
('EAM_Fe_Mendelev_2003',
 'EAM Potential for Fe (Mendelev et al.)',
 'EAM', 'eam/alloy', 'eam.alloy',
 ARRAY['Fe'], 'Pure iron',
 ARRAY['结构材料', '纯铁'],
 'Well-established EAM potential for BCC iron.',
 '{"temperatureRange": [300, 2000], "phases": ["BCC", "liquid"]}',
 '[{"citation": "Mendelev et al. (2003)"}]',
 '[{"name": "M.I. Mendelev", "affiliation": "Ames Laboratory"}]',
 '{}',
 ARRAY['LAMMPS'],
 '{"pair_style": "eam/alloy", "pair_coeff": "* * Fe_mendelev.eam.alloy Fe"}',
 'NIST IPR',
 ARRAY['结构材料', 'Fe', 'EAM', 'LAMMPS'],
 '{"irradiationRelevant": false, "hasDefectData": false, "hasLiquidPhase": true, "validationLevel": "basic"}'
)`,

    `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra) VALUES
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
)`
  ]

  for (let i = 0; i < seeds.length; i++) {
    try {
      await client.query(seeds[i])
      console.log(`✅ Seed ${i + 1}/10 inserted`)
    } catch (err) {
      console.error(`❌ Seed ${i + 1} failed:`, err.message)
    }
  }

  // Verify
  const result = await client.query('SELECT count(*) FROM potentials')
  console.log(`\n🎉 Total potentials: ${result.rows[0].count}`)

  await client.end()
}

main().catch(err => {
  console.error('Fatal:', err)
  process.exit(1)
})
