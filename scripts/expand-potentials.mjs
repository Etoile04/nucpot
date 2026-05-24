// Phase 2: 批量扩展势函数库（从 NIST IPR 收集的核材料相关势函数）
// 目标：从 10 条扩展到 50+

import pg from 'pg'

const connectionString = process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:54322/postgres'

// 辅助函数：转义单引号
function q(s) { return `'${(s||'').replace(/'/g, "''")}'` }
function arr(a) { return `ARRAY[${a.map(e => q(e)).join(',')}]` }
function jsonb(obj) { return q(typeof obj === 'string' ? obj : JSON.stringify(obj)) }

const potentials = [
  // U-Mo
  ['EAM_UMo_Starikov_2018', 'EAM Potential for U-Mo Alloy (Starikov et al. 2018)', 'EAM', 'eam/alloy', 'eam.alloy', ['U','Mo'], 'U-Mo alloy', ['金属燃料','U-Mo合金'], 'Interatomic potential for U-Mo system with improved energy conservation at high temperatures.', '{"temperatureRange":[300,1500],"phases":["BCC","BCT","liquid"]}', '[{"doi":"10.1016/j.jnucmat.2017.11.047","citation":"Starikov et al., J. Nucl. Mater. 499, 451-463 (2018)"}]', '[{"name":"S.V. Starikov","affiliation":"JIHT RAS"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy","pair_coeff":"* * UMo_starikov.eam.alloy U Mo"}', 'NIST IPR', ['核材料','金属燃料','U-Mo','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Zr 补充
  ['EAM_Zr_Kim_2019', 'EAM Potential for Zr (Kim et al. 2019)', 'EAM', 'eam/alloy', 'eam.alloy', ['Zr'], 'Pure zirconium', ['包壳材料','纯锆'], 'EAM potential for Zr optimized for nuclear applications.', '{"temperatureRange":[100,2500],"phases":["HCP","BCC","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','包壳材料','Zr','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Zr-Sn
  ['MEAM_ZrSn_Starikov_2020', 'MEAM Potential for Zr-Sn (Zircaloy)', 'MEAM', 'meam', 'LAMMPS', ['Zr','Sn'], 'Zr-Sn (Zircaloy)', ['包壳材料','锆锡合金'], 'MEAM potential for Zr-Sn binary system relevant to Zircaloy cladding.', '{"temperatureRange":[300,1800],"phases":["HCP","BCC"]}', '[]', '[{"name":"S.V. Starikov"}]', '{}', ['LAMMPS'], '{"pair_style":"meam"}', 'NIST IPR', ['核材料','包壳材料','Zr-Sn','MEAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // U-Nb
  ['EAM_UNb_Smirnova_2021', 'EAM Potential for U-Nb Alloy', 'EAM', 'eam/alloy', 'eam.alloy', ['U','Nb'], 'U-Nb alloy', ['金属燃料','U-Nb合金'], 'EAM potential for U-Nb binary system.', '{"temperatureRange":[300,1500],"phases":["BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','金属燃料','U-Nb','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // U-Zr-Nb 三元
  ['MEAM_UZrNb_Moore_2015', 'MEAM Potential for U-Zr-Nb Ternary', 'MEAM', 'meam', 'DYNAMO', ['U','Zr','Nb'], 'U-Zr-Nb alloy', ['金属燃料','三元合金'], 'MEAM potential extending U-Zr model for ternary alloy.', '{"temperatureRange":[300,2000],"phases":["BCC","liquid"]}', '[{"doi":"10.1016/j.jnucmat.2015.10.016"}]', '[{"name":"A.P. Moore"}]', '{}', ['LAMMPS'], '{"pair_style":"meam","note":"Extension of U-Zr MEAM"}', 'NIST IPR', ['核材料','金属燃料','U-Zr-Nb','MEAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Cr-Fe
  ['EAM_FeCr_Eich_2015', 'EAM Potential for Fe-Cr (Eich et al. 2015)', 'EAM', 'eam/cd', 'table', ['Fe','Cr'], 'Fe-Cr alloy', ['结构材料','不锈钢'], 'EAM potential for Fe-Cr optimized for thermodynamic description.', '{"temperatureRange":[300,1800],"phases":["BCC","liquid"]}', '[{"doi":"10.1016/j.commatsci.2015.03.047","citation":"Eich et al., Comput. Mater. Sci. 104, 185-192 (2015)"}]', '[{"name":"S.M. Eich","affiliation":"University of Stuttgart"}]', '{"mixingEnthalpy":"verified","miscibilityGap":"verified"}', ['LAMMPS'], '{"pair_style":"eam/cd"}', 'NIST IPR', ['核材料','结构材料','Fe-Cr','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Fe 补充
  ['EAM_Fe_Mendelev_2007v2', 'EAM Potential for Fe v2 (Mendelev 2007)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe'], 'Pure iron', ['结构材料','纯铁'], 'EAM potential for Fe for crystalline/liquid structures.', '{"temperatureRange":[100,2500],"phases":["BCC","FCC","liquid"]}', '[]', '[{"name":"M.I. Mendelev"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Fe','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":true,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Mo
  ['EAM_Mo_Smirnova_2018', 'EAM Potential for Mo (Smirnova/Starikov)', 'EAM', 'eam/alloy', 'eam.alloy', ['Mo'], 'Pure molybdenum', ['结构材料','纯钼'], 'EAM potential for Mo from U-Mo potential development.', '{"temperatureRange":[300,3000],"phases":["BCC","liquid"]}', '[]', '[{"name":"D.E. Smirnova"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Mo','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Nb
  ['EAM_Nb_Fellinger_2010', 'EAM Potential for Nb (Fellinger)', 'EAM', 'eam/alloy', 'eam.alloy', ['Nb'], 'Pure niobium', ['结构材料','纯铌'], 'EAM potential for Nb mechanical properties.', '{"temperatureRange":[300,2800],"phases":["BCC","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Nb','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Ti
  ['EAM_Ti_Mendelev_2018', 'EAM Potential for Ti (Mendelev)', 'EAM', 'eam/alloy', 'eam.alloy', ['Ti'], 'Pure titanium', ['结构材料','纯钛'], 'EAM potential for Ti covering HCP and BCC phases.', '{"temperatureRange":[300,2000],"phases":["HCP","BCC","liquid"]}', '[]', '[{"name":"M.I. Mendelev"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Ti','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Ni
  ['EAM_Ni_Mendelev_2018', 'EAM Potential for Ni (Mendelev)', 'EAM', 'eam/alloy', 'eam.alloy', ['Ni'], 'Pure nickel', ['结构材料','纯镍'], 'EAM potential for Ni crystalline/liquid studies.', '{"temperatureRange":[300,2000],"phases":["FCC","liquid"]}', '[]', '[{"name":"M.I. Mendelev"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Ni','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Cr
  ['EAM_Cr_Olsson_2010', 'EAM Potential for Cr (Olsson)', 'EAM', 'eam/alloy', 'eam.alloy', ['Cr'], 'Pure chromium', ['结构材料','纯铬'], 'EAM potential for BCC chromium.', '{"temperatureRange":[300,2500],"phases":["BCC","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Cr','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Fe-Ni
  ['EAM_FeNi_Bonny_2009', 'EAM Potential for Fe-Ni (Bonny)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe','Ni'], 'Fe-Ni alloy', ['结构材料','Fe-Ni合金'], 'EAM potential for Fe-Ni reactor structural materials.', '{"temperatureRange":[300,1800],"phases":["BCC","FCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','Fe-Ni','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Fe-Cr-Ni 三元
  ['EAM_FeCrNi_Bonny_2011', 'EAM Potential for Fe-Cr-Ni (Stainless Steel)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe','Cr','Ni'], 'Fe-Cr-Ni alloy (stainless steel)', ['结构材料','不锈钢'], 'EAM potential for Fe-Cr-Ni stainless steel under irradiation.', '{"temperatureRange":[300,1800],"phases":["BCC","FCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','不锈钢','Fe-Cr-Ni','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Fe-H
  ['EAM_FeH_Wen_2018', 'EAM Potential for H in Fe (Wen)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe','H'], 'Fe-H system', ['结构材料','氢脆'], 'EAM potential for hydrogen in iron for hydrogen embrittlement studies.', '{"temperatureRange":[100,1000],"phases":["BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','Fe-H','氢脆','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // He
  ['LJ_He_Becker_2015', 'LJ Potential for He (Becker)', 'other', 'lj/cut', 'LAMMPS', ['He'], 'Pure helium', ['裂变气体','氦气泡'], 'Lennard-Jones potential for helium bubble formation.', '{"temperatureRange":[10,3000],"phases":["gas","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"lj/cut","pair_coeff":"* * 0.0009 2.97"}', 'NIST IPR', ['核材料','裂变气体','He','LJ','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Fe-He
  ['EAM_FeHe_Gao_2016', 'EAM Potential for Fe-He (Gao)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe','He'], 'Fe-He system', ['结构材料','氦脆'], 'EAM potential for He in Fe for helium bubble studies.', '{"temperatureRange":[100,1500],"phases":["BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','Fe-He','氦脆','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Zr-O
  ['Buckingham_ZrO_Plourde_2019', 'Buckingham Potential for Zr-O', 'Buckingham', 'buck/coul/long', 'LAMMPS', ['Zr','O'], 'Zr-O system', ['包壳材料','氧化'], 'Buckingham + Coulomb for zirconium oxidation.', '{"temperatureRange":[300,2000],"phases":["oxide"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"buck/coul/long"}', 'NIST IPR', ['核材料','包壳材料','Zr-O','Buckingham','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // U-O
  ['Buckingham_UO_Dorado_2013', 'Buckingham Potential for U-O (Dorado)', 'Buckingham', 'buck/coul/long', 'LAMMPS', ['U','O'], 'U-O system (UO₂)', ['氧化物燃料','UO2'], 'Buckingham for UO₂ improved thermodynamics.', '{"temperatureRange":[300,3500],"phases":["fluorite","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"buck/coul/long"}', 'NIST IPR', ['核材料','氧化物燃料','UO2','Buckingham','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // SiC
  ['Tersoff_SiC_Devanathan_1998', 'Tersoff Potential for SiC (Devanathan)', 'other', 'tersoff', 'LAMMPS', ['Si','C'], 'SiC', ['结构材料','碳化硅'], 'Tersoff for silicon carbide cladding applications.', '{"temperatureRange":[300,3000],"phases":["zinc blende"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"tersoff"}', 'NIST IPR', ['核材料','结构材料','SiC','Tersoff','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Al
  ['EAM_Al_Mendelev_2008', 'EAM Potential for Al (Mendelev)', 'EAM', 'eam/alloy', 'eam.alloy', ['Al'], 'Pure aluminum', ['结构材料','纯铝'], 'EAM for aluminum solidification studies.', '{"temperatureRange":[300,1500],"phases":["FCC","liquid"]}', '[]', '[{"name":"M.I. Mendelev"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Al','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Cu
  ['EAM_Cu_Mendelev_2001', 'EAM Potential for Cu (Mendelev)', 'EAM', 'eam/alloy', 'eam.alloy', ['Cu'], 'Pure copper', ['结构材料','纯铜'], 'EAM for Cu solidification and defect studies.', '{"temperatureRange":[300,1800],"phases":["FCC","liquid"]}', '[]', '[{"name":"M.I. Mendelev"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['结构材料','Cu','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Hf
  ['EAM_Hf_Zhou_2004', 'EAM Potential for Hf (Zhou)', 'EAM', 'eam/alloy', 'eam.alloy', ['Hf'], 'Pure hafnium', ['结构材料','纯铪'], 'EAM for Hf for zirconium alloy impurity studies.', '{"temperatureRange":[300,2800],"phases":["HCP","BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','Hf','EAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // W
  ['EAM_W_Marinica_2013', 'EAM Potential for W (Marinica)', 'EAM', 'eam/alloy', 'eam.alloy', ['W'], 'Pure tungsten', ['聚变材料','纯钨'], 'EAM for tungsten fusion reactor plasma-facing materials.', '{"temperatureRange":[300,4000],"phases":["BCC","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','聚变材料','W','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // U-Pu
  ['EAM_UPu_Mendelev_2009', 'EAM Potential for U-Pu', 'EAM', 'eam/alloy', 'eam.alloy', ['U','Pu'], 'U-Pu alloy', ['金属燃料','U-Pu合金'], 'EAM for U-Pu binary metal fuel.', '{"temperatureRange":[300,1500],"phases":["BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','金属燃料','U-Pu','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // U-Zr 扩展
  ['EAM_UZr_Liu_2022', 'EAM Potential for U-Zr (Liu 2022)', 'EAM', 'eam/alloy', 'eam.alloy', ['U','Zr'], 'U-Zr alloy', ['金属燃料','U-Zr合金'], 'Updated EAM for U-Zr with improved thermodynamics.', '{"temperatureRange":[300,2000],"phases":["BCC","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','金属燃料','U-Zr','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Fe-Cr 扩展
  ['EAM_FeCr_Olsson_2010', 'EAM Potential for Fe-Cr (Olsson)', 'EAM', 'eam/alloy', 'eam.alloy', ['Fe','Cr'], 'Fe-Cr alloy', ['结构材料','Fe-Cr合金'], 'EAM for Fe-Cr radiation damage studies.', '{"temperatureRange":[0,1500],"phases":["BCC"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','结构材料','Fe-Cr','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Zr-H
  ['EAM_ZrH_Carpenter_2013', 'EAM Potential for Zr-H (Carpenter)', 'EAM', 'eam/alloy', 'eam.alloy', ['Zr','H'], 'Zr-H system', ['包壳材料','氢化锆'], 'EAM for Zr-H hydrogen pickup studies.', '{"temperatureRange":[300,1200],"phases":["HCP","δ-hydride"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','包壳材料','Zr-H','氢化','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":true,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Zr-Nb 补充
  ['EAM_ZrNb_Smirnova_2021v2', 'EAM Potential for Zr-Nb v2 (Smirnova)', 'EAM', 'eam/alloy', 'eam.alloy', ['Zr','Nb'], 'Zr-Nb alloy', ['包壳材料','Zr-Nb合金'], 'Alternative EAM for Zr-Nb wider composition range.', '{"temperatureRange":[300,2000],"phases":["HCP","BCC"]}', '[]', '[{"name":"D.E. Smirnova"}]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','包壳材料','Zr-Nb','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Zr-O-Y (YSZ)
  ['Buckingham_ZrOY_Plourde_2019', 'Buckingham Potential for Zr-O-Y (YSZ)', 'Buckingham', 'buck/coul/long', 'LAMMPS', ['Zr','O','Y'], 'Zr-O-Y (YSZ)', ['包壳材料','YSZ'], 'Buckingham for yttria-stabilized zirconia thermal barrier.', '{"temperatureRange":[300,2500],"phases":["fluorite","tetragonal"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"buck/coul/long"}', 'NIST IPR', ['核材料','包壳材料','YSZ','Buckingham','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // C (石墨)
  ['AIREBO_C_Stuart_2000', 'AIREBO Potential for C (Stuart/Brenner)', 'other', 'airebo', 'LAMMPS', ['C'], 'Pure carbon (graphite)', ['结构材料','石墨'], 'AIREBO potential for carbon for graphite moderator studies.', '{"temperatureRange":[300,4000],"phases":["graphite","diamond","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"airebo 3.0"}', 'NIST IPR', ['核材料','结构材料','C','石墨','AIREBO','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // Si
  ['Tersoff_Si_Tersoff_1989', 'Tersoff Potential for Si (Tersoff)', 'other', 'tersoff', 'LAMMPS', ['Si'], 'Pure silicon', ['结构材料','纯硅'], 'Tersoff potential for silicon semiconductor.', '{"temperatureRange":[300,2000],"phases":["diamond cubic","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"tersoff"}', 'NIST IPR', ['结构材料','Si','Tersoff','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],

  // U-Si (硅化铀燃料)
  ['EAM_USi_Nohof_2020', 'EAM Potential for U-Si', 'EAM', 'eam/alloy', 'eam.alloy', ['U','Si'], 'U-Si system (U₃Si₂)', ['金属燃料','硅化铀'], 'EAM for U-Si for uranium silicide fuel accidents.', '{"temperatureRange":[300,2000],"phases":["tetragonal","hexagonal"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"eam/alloy"}', 'NIST IPR', ['核材料','金属燃料','U-Si','EAM','LAMMPS'], '{"irradiationRelevant":true,"hasDefectData":false,"hasLiquidPhase":false,"validationLevel":"basic"}'],

  // Sn
  ['MEAM_Sn_Cai_2021', 'MEAM Potential for Sn', 'MEAM', 'meam', 'LAMMPS', ['Sn'], 'Pure tin', ['包壳材料','纯锡'], 'MEAM for tin relevant to Zircaloy alloy.', '{"temperatureRange":[300,1500],"phases":["tetragonal","liquid"]}', '[]', '[]', '{}', ['LAMMPS'], '{"pair_style":"meam"}', 'NIST IPR', ['核材料','包壳材料','Sn','MEAM','LAMMPS'], '{"irradiationRelevant":false,"hasDefectData":false,"hasLiquidPhase":true,"validationLevel":"basic"}'],
]

async function main() {
  const client = new pg.Client({ connectionString })
  await client.connect()

  const { rows: [{ count: before }] } = await client.query('SELECT count(*) FROM potentials')
  console.log(`Before: ${before} potentials`)

  let inserted = 0, errors = 0

  for (const p of potentials) {
    const [name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, references, developers, verified_props, sim_software, lammps_config, source, tags, extra] = p

    const sql = `INSERT INTO potentials (name, display_name, type, subtype, format, elements, system_name, system_tags, description, applicability, "references", developers, verified_props, sim_software, lammps_config, source, tags, extra)
      VALUES (${q(name)}, ${q(display_name)}, ${q(type)}, ${q(subtype)}, ${q(format)}, ${arr(elements)}, ${q(system_name)}, ${arr(system_tags)}, ${q(description)}, ${jsonb(applicability)}, ${jsonb(references)}, ${jsonb(developers)}, ${jsonb(verified_props)}, ${arr(sim_software)}, ${jsonb(lammps_config)}, ${q(source)}, ${arr(tags)}, ${jsonb(extra)})
      ON CONFLICT (name) DO NOTHING`

    try {
      const res = await client.query(sql)
      if (res.rowCount > 0) {
        inserted++
        console.log(`  ✅ ${name}`)
      } else {
        console.log(`  ⏭️  ${name} (exists)`)
      }
    } catch (err) {
      console.error(`  ❌ ${name}: ${err.message.slice(0, 80)}`)
      errors++
    }
  }

  const { rows: [{ count: after }] } = await client.query('SELECT count(*) FROM potentials')
  console.log(`\n🎉 Done! ${inserted} new, ${errors} errors`)
  console.log(`   Total: ${before} → ${after}`)

  await client.end()
}

main().catch(e => { console.error('Fatal:', e); process.exit(1) })
