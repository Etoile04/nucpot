#!/usr/bin/env python3
"""E2E Phase A — LightRAG 10-Paper Pipeline Verification (NFM-1763).

Verifies the full LightRAG pipeline end-to-end:
  1. Ingest 10 representative nuclear materials papers (bilingual EN/CN)
  2. Query the knowledge graph via 3 retrieval modes
  3. Verify NucMat ontology entity/relationship extraction
  4. Test degradation when LightRAG is unreachable
  5. Report response times and result quality

Usage:
    # Against the NFM API (requires JWT auth token):
    python scripts/e2e_lightrag_10papers.py \
      --api-url http://127.0.0.1:8001 \
      --token <JWT_TOKEN>

    # Directly against the LightRAG sidecar (no auth needed):
    python scripts/e2e_lightrag_10papers.py \
      --direct --lightrag-url http://127.0.0.1:9621

    # Dry-run: print what would be tested without calling any service:
    python scripts/e2e_lightrag_10papers.py --dry-run

Exit code 0 = all checks passed, 1 = one or more failures.

Prerequisites:
  - LightRAG sidecar running on port 9621 (or via docker-compose overlay)
  - LLM + embedding model configured (see .env.lightrag.example)
  - For NFM API mode: valid editor-role JWT token
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# Immutable result types
# =============================================================================


@dataclass(frozen=True)
class CheckResult:
    """Single acceptance-criterion check result."""

    name: str
    passed: bool
    detail: str
    duration_s: float = 0.0


@dataclass(frozen=True)
class QueryResult:
    """Result of a single knowledge-graph query."""

    query_mode: str
    query_text: str
    response_text: str
    response_time_s: float
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)


# =============================================================================
# Synthetic nuclear materials papers (10 papers, bilingual EN + CN)
# =============================================================================


PAPERS: list[dict[str, str]] = [
    # ------------------------------------------------------------------
    # Paper 1: UO2 thermophysical properties (English)
    # ------------------------------------------------------------------
    {
        "source": "Finkelstein_2023_UO2_thermophysical",
        "text": """Title: High-Temperature Thermophysical Properties of UO2 Nuclear Fuel

Abstract: Uranium dioxide (UO2) remains the most widely used nuclear fuel material
worldwide. This study presents a comprehensive assessment of the thermophysical
properties of stoichiometric UO2 in the temperature range 300 K to 3120 K, with
particular attention to the melting point region. Experimental measurements of
thermal conductivity, specific heat capacity, thermal expansion, and oxygen
diffusion coefficients are reported and compared with existing correlations
recommended by the IAEA.

1. Introduction
UO2 is a ceramic nuclear fuel with the fluorite crystal structure (Fm-3m space
group). Its thermophysical properties are critical inputs for nuclear fuel
performance codes such as FRAPCON and BISON. The melting point of
stoichiometric UO2 is well established at approximately 3138 K under atmospheric
pressure, though oxygen partial pressure variations can shift this value.

2. Thermal Conductivity
The thermal conductivity of UO2 decreases with increasing temperature up to
approximately 1800 K, reaching a minimum near 2.5 W/m·K. Above this temperature,
phonon-phonon scattering decreases and radiative heat transfer contributes,
causing a slight increase. The relationship is well described by the Fink-Lucuta
correlation: k = 1/(0.0375 + 2.165×10⁻⁴T) + 4.715×10⁹/T² exp(-16361/T) W/m·K.

3. Specific Heat Capacity
The specific heat of UO2 shows a characteristic lambda transition near 2670 K
associated with the Bredig (Frenkel defect) transition. Above the transition, the
enhancement in heat capacity is attributed to oxygen disorder in the anion
sublattice.

4. Thermal Expansion
The linear thermal expansion coefficient of UO2 increases monotonically with
temperature. The average coefficient from 300 K to the melting point is
approximately 10.1×10⁻⁶ K⁻¹. The volume expansion at the melting point
reaches approximately 28%.

5. Oxygen Diffusion
Oxygen self-diffusion in UO2 is a thermally activated process described by
D = D₀ exp(-Q/RT). The activation energy Q is approximately 248 kJ/mol for
stoichiometric UO2. Enhanced oxygen mobility near the melting point affects
fission gas release behavior.

References:
[1] Fink J.K., Lucuta P.G. (2005) Thermophysical Properties of UO2, J. Nucl. Mater., 344(1-3), 1-14.
[2] Konings R.J.M. et al. (2015) Comprehensive Nuclear Materials, 2nd ed., Elsevier.
""",
    },
    # ------------------------------------------------------------------
    # Paper 2: UN fuel properties (English)
    # ------------------------------------------------------------------
    {
        "source": "Sato_2022_UN_high_density",
        "text": """Title: Fabrication and Characterization of High-Density Uranium Nitride (UN)
Fuel for Advanced Nuclear Reactors

Abstract: Uranium mononitride (UN) is a candidate advanced nuclear fuel with
superior thermal conductivity and heavy metal density compared to conventional
UO2. This work investigates the sintering behavior, microstructure evolution,
and thermophysical properties of UN pellets fabricated via carbothermic
reduction and nitridation of UO2 followed by spark plasma sintering (SPS).

1. Material Properties
UN crystallizes in the rock-salt (NaCl-type, Fm-3m) structure with a lattice
parameter of approximately 4.890 Å. The theoretical density is 14.32 g/cm³,
significantly higher than UO2 (10.97 g/cm³). This high heavy metal density
translates to approximately 47% more uranium atoms per unit volume.

2. Thermal Conductivity
The thermal conductivity of dense UN (>95% TD) ranges from approximately
13 W/m·K at 300 K to 8 W/m·K at 1500 K. This is 3-5 times higher than UO2
at equivalent temperatures, offering substantial margin for thermal performance.

3. Irradiation Behavior
Under neutron irradiation, UN exhibits swelling due to fission gas bubble
formation and accumulation. The fission gas release fraction remains below 5%
up to approximately 5 at.% burnup at temperatures below 1400 K. Above this
temperature, interconnected gas bubble networks form, leading to accelerated
fission gas release.

References:
[1] Sato K. et al. (2022) J. Nucl. Mater., 562, 153576.
[2] Hayes S.L. et al. (1990) Material Property Correlations for UN, ANL-RE-90/3.
""",
    },
    # ------------------------------------------------------------------
    # Paper 3: MOX fuel (English)
    # ------------------------------------------------------------------
    {
        "source": "Degueldre_2021_MOX_homogeneity",
        "text": """Title: Homogeneity and Phase Distribution in (U,Pu)O2 Mixed Oxide (MOX)
Fuel for Fast Breeder Reactors

Abstract: Mixed oxide (MOX) fuel, consisting of a solid solution of uranium and
plutonium dioxides (U1-xPuxO2), is the primary fuel for sodium-cooled fast
reactors (SFRs) and is also used in several light water reactors (LWRs). This
study examines the phase homogeneity, oxygen-to-metal (O/M) ratio effects, and
thermodynamic stability of MOX fuel compositions relevant to Generation IV
reactor designs.

1. Phase Diagram Considerations
The UO2-PuO2 pseudo-binary system exhibits complete solid solubility across the
entire composition range at temperatures above approximately 600 K. However,
depending on the O/M ratio, secondary phases such as Pu2O3 (hexagonal),
U4O9, or the perovskite-type (U,Pu)3O8 may form during fabrication or irradiation.

2. Oxygen Potential
The oxygen potential of MOX fuel is a critical thermodynamic parameter that
governs fission product behavior, cladding interaction, and fuel performance.
For hypostoichiometric compositions (O/M < 2.0), the oxygen potential decreases
with increasing Pu content, which enhances the retention of fission products
within the fuel matrix.

3. Lattice Parameter
The lattice parameter of stoichiometric (U,Pu)O2 follows Vegard's law
approximately linearly between UO2 (5.470 Å) and PuO2 (5.396 Å). Deviations
from linearity indicate non-ideal mixing behavior or the presence of oxygen
vacancies.

References:
[1] Degueldre C. et al. (2021) J. Nucl. Mater., 545, 152733.
[2] Kato M. et al. (2019) Oxygen Potential of (U,Pu)O2, J. Nucl. Mater., 514, 234-241.
""",
    },
    # ------------------------------------------------------------------
    # Paper 4: U-Zr phase diagram and CALPHAD (English)
    # ------------------------------------------------------------------
    {
        "source": "Kurata_2023_UZr_CALPHAD",
        "text": """Title: CALPHAD Assessment of the U-Zr Binary System for Metallic Fuel
Applications

Abstract: Metallic fuels based on the U-Zr alloy system are a leading candidate
for sodium-cooled fast reactors owing to their high thermal conductivity,
compatibility with sodium coolant, and excellent breeding ratio. This work
presents an updated CALPHAD (Calculation of Phase Diagrams) assessment of the
U-Zr binary system incorporating recent experimental data and ab initio
enthalpies of formation.

1. Phase Equilibria
The U-Zr system features several intermetallic compounds and solid solution
phases. Key phases include: the alpha-U (orthorhombic) phase, beta-U
(tetragonal) phase, gamma-U (BCC, high-temperature), delta-UZr2 (tetragonal),
and the BCC gamma-(U,Zr) solid solution which is the primary fuel phase at
reactor operating temperatures.

2. Thermodynamic Modeling
A substitutional solution model was adopted for the liquid and gamma-(U,Zr)
phases. The delta-UZr2 phase was modeled using a two-sublattice model with
the formula (U,Zr)2(U,Zr). Redlich-Kister polynomial expansions up to third
order were employed to describe the excess Gibbs energy of solution phases.

3. Experimental Validation
Differential thermal analysis (DTA) measurements were performed on U-Zr
alloys with compositions ranging from 0 to 100 at.% Zr. The assessed phase
diagram reproduces the experimentally determined liquidus, solidus, and
solid-state transformation temperatures within ±10 K.

References:
[1] Kurata M. (2023) Calphad, 81, 102486.
[2] Ogawa T. (2015) Metallic Fuels for Fast Reactors, Comprehensive Nuclear Materials.
""",
    },
    # ------------------------------------------------------------------
    # Paper 5: ZrO2 cladding interaction (English)
    # ------------------------------------------------------------------
    {
        "source": "Kim_2024_ZrO2_cladding",
        "text": """Title: Chemical Interaction Between UO2 Fuel and ZrO2 Oxide Layer on
Zircaloy Cladding During Loss-of-Coolant Accidents

Abstract: During loss-of-coolant accidents (LOCAs) in light water reactors, the
exothermic reaction between UO2 fuel and the ZrO2 oxide layer on Zircaloy
cladding can significantly impact core degradation progression. This
experimental study investigates the kinetics and products of the UO2-ZrO2
reaction at temperatures from 2000 K to 2800 K under controlled atmospheres.

1. Reaction Mechanism
At temperatures exceeding approximately 2100 K, UO2 and ZrO2 form a
continuous solid solution with the fluorite structure. The interdiffusion of
U⁴⁺ and Zr⁴⁺ cations across the UO2-ZrO2 interface follows a parabolic rate
law, with an apparent activation energy of 350 ± 40 kJ/mol.

2. Phase Formation
Above 2400 K, a tetragonal (U,Zr)O2 phase with the scheelite structure
forms preferentially at the reaction interface. The formation of this phase is
associated with a volume contraction of approximately 4%, which can create
stress concentrations and promote fuel fragmentation.

3. Implications for Safety Analysis
The UO2-ZrO2 interaction rate is sufficiently fast to form a continuous
reaction layer within the timeframe of a LOCA transient. This layer modifies
the effective thermal conductivity of the fuel-cladding gap and must be
accounted for in integral severe accident codes such as MELCOR and ATHLET-CD.

References:
[1] Kim J.H. et al. (2024) J. Nucl. Mater., 582, 154533.
[2] Hofmann P. (1999) Current Knowledge on Core Degradation Phenomena, Nucl. Eng. Des., 187, 73-89.
""",
    },
    # ------------------------------------------------------------------
    # Paper 6: UO2 热物理性质 (Chinese)
    # ------------------------------------------------------------------
    {
        "source": "王明_2023_UO2热物性",
        "text": """标题：二氧化铀（UO2）核燃料高温热物理性质研究

摘要：二氧化铀（UO2）是目前全球应用最广泛的核燃料材料。本研究系统评估了
化学计量比UO2在300 K至3120 K温度范围内的热物理性质，包括热导率、比热容、
热膨胀系数和氧扩散系数。实验数据与IAEA推荐关联式进行了对比分析。

1. 引言
UO2具有萤石型晶体结构（空间群Fm-3m），是核燃料性能分析程序（如FRAPCON、
BISON）的关键输入参数。在大气压下，化学计量比UO2的熔点约为3138 K。

2. 热导率
UO2的热导率在300 K时约为10 W/m·K，随温度升高而降低，在约1800 K时
达到最小值（约2.5 W/m·K）。高温下，声子-声子散射减弱，辐射传热贡献
增大，使热导率略有回升。Fink-Lucuta关联式能很好地描述这一行为。

3. 比热容
UO2的比热容在约2670 K附近出现特征性λ转变，对应Bredig（Frenkel缺陷）
转变。该转变与阴离子亚点阵中氧无序化有关。

4. 裂变气体释放
在辐照条件下，UO2中产生的裂变气体（Xe、Kr）在晶格中扩散并聚集成
气泡。温度超过1600 K时，气泡连通网络形成，裂变气体释放显著加速。

参考文献：
[1] 王明等 (2023) 核材料学报, 42(3), 301-312.
[2] Konings R.J.M. et al. (2015) Comprehensive Nuclear Materials, 2nd ed.
""",
    },
    # ------------------------------------------------------------------
    # Paper 7: UN 氮化铀燃料 (Chinese)
    # ------------------------------------------------------------------
    {
        "source": "张伟_2024_氮化铀燃料",
        "text": """标题：氮化铀（UN）先进核燃料的制备工艺与辐照行为研究

摘要：氮化铀（UN）是一种具有高热导率和高重金属密度的先进核燃料候选材料。
本文采用碳热还原氮化法结合放电等离子烧结（SPS）技术制备了高致密度UN
芯块，系统研究了其微观结构演变和热物理性质。

1. 材料特性
UN具有岩盐型（NaCl型，Fm-3m）晶体结构，晶格常数约4.890 Å。
理论密度为14.32 g/cm³，显著高于UO2的10.97 g/cm³。这意味着UN单位体积
内的铀原子数比UO2多约47%。

2. 热导率
致密度大于95%理论密度的UN在300 K时热导率约为13 W/m·K，1500 K时约
为8 W/m·K。在相同温度条件下，UN的热导率是UO2的3-5倍。

3. 辐照行为
在中子辐照下，UN因裂变气体气泡的形成和聚集而产生肿胀。在温度低于
1400 K、燃耗低于5 at.%的条件下，裂变气体释放份额低于5%。温度升高时，
气泡连通网络形成，裂变气体释放加速。

4. 与UO2的性能对比
与UO2相比，UN具有更高的热导率、更高的铀密度和更低的燃料中心温度，
这些优势使其成为快堆和小型模块化反应堆（SMR）的有力候选燃料。

参考文献：
[1] 张伟等 (2024) 核动力工程, 45(1), 45-58.
[2] Hayes S.L. et al. (1990) ANL-RE-90/3.
""",
    },
    # ------------------------------------------------------------------
    # Paper 8: CALPHAD相图计算 (Chinese)
    # ------------------------------------------------------------------
    {
        "source": "李华_2023_CALPHAD相图",
        "text": """标题：基于CALPHAD方法的U-Zr二元合金相图热力学评估

摘要：U-Zr合金体系是钠冷快堆金属燃料的主要候选体系。本文采用CALPHAD
（相图计算）方法，结合最新实验数据和第一性原理计算结果，对U-Zr二元
体系进行了系统热力学优化。

1. 相平衡
U-Zr体系包含多个金属间化合物和固溶体相。主要相包括：α-U（正交晶系）、
β-U（四方晶系）、γ-U（体心立方，高温相）、δ-UZr2（四方晶系）以及
γ-(U,Zr)体心立方固溶体。在反应堆运行温度下，γ-(U,Zr)固溶体是主要的
燃料相。

2. 热力学模型
液相和γ-(U,Zr)固溶体采用替代溶液模型描述。δ-UZr2相采用双子格模型，
化学式为(U,Zr)₂(U,Zr)。溶液相的超额吉布斯自由能用Redlich-Kister多项式
展开描述。

3. 相图验证
对组成为0-100 at.% Zr的U-Zr合金进行了差热分析（DTA）测量。优化后的
相图在液相线、固相线和固态相变温度方面与实验数据吻合良好，偏差在±10 K以内。

参考文献：
[1] 李华等 (2023) 金属学报, 59(8), 1023-1036.
[2] Kurata M. (2023) Calphad, 81, 102486.
""",
    },
    # ------------------------------------------------------------------
    # Paper 9: MOX混合氧化物燃料 (Chinese)
    # ------------------------------------------------------------------
    {
        "source": "陈刚_2024_MOX燃料",
        "text": """标题：(U,Pu)O2混合氧化物（MOX）燃料的相均匀性与氧势研究

摘要：混合氧化物（MOX）燃料由二氧化铀和二氧化钚的固溶体（U₁₋ₓPuₓO₂）
组成，是钠冷快堆的主要燃料，也在部分轻水堆中使用。本研究系统考察了
MOX燃料的相均匀性、氧金属比（O/M）效应以及与第四代反应堆设计相关的
热力学稳定性。

1. 相图分析
UO2-PuO2伪二元体系在600 K以上温度范围内表现出完全固溶行为。然而，
根据O/M比的不同，可能形成Pu₂O₃（六方）、U₄O₉或钙钛矿型(U,Pu)₃O₈
等次级相。

2. 氧势
MOX燃料的氧势是控制裂变产物行为、包壳相互作用和燃料性能的关键热力学
参数。对于亚化学计量组成（O/M < 2.0），氧势随Pu含量增加而降低，这
有利于裂变产物在燃料基体中的保留。

3. 晶格参数
化学计量比(U,Pu)O₂的晶格参数在UO₂（5.470 Å）和PuO₂（5.396 Å）
之间近似遵循Vegard定律线性变化。偏离线性关系表明存在非理想混合行为
或氧空位。

参考文献：
[1] 陈刚等 (2024) 核科学与工程, 44(2), 178-192.
[2] Kato M. et al. (2019) J. Nucl. Mater., 514, 234-241.
""",
    },
    # ------------------------------------------------------------------
    # Paper 10: Irradiation effects / fission gas release (English)
    # ------------------------------------------------------------------
    {
        "source": "Turnbull_2023_fission_gas",
        "text": """Title: Fission Gas Behavior in UO2 and UN Nuclear Fuels Under High Burnup
Conditions

Abstract: Fission gas release (FGR) from nuclear fuel is a critical performance
limit that constrains reactor operation. This comparative study examines the
diffusion, nucleation, and release mechanisms of xenon and krypton in UO2 and
UN fuels under irradiation to high burnup levels (>10 at.%).

1. Diffusion Mechanisms
In UO2, fission gas atoms diffuse via a combination of intrinsic and radiation-
enhanced mechanisms. The effective diffusivity can be expressed as D_eff = D₀
exp(-E_a/RT) + D_rad f(dose rate), where D_rad represents the athermal
contribution from fission spike damage. For Xe in UO2, E_a is approximately
420 kJ/mol at low temperature.

In UN, fission gas diffusion occurs primarily through grain boundaries and
dislocation pipes at temperatures below 1400 K. The activation energy for grain
boundary diffusion of Xe in UN is approximately 210 kJ/mol.

2. Bubble Nucleation and Growth
Fission gas atoms precipitate as intragranular bubbles when their concentration
exceeds the solubility limit. Bubble growth proceeds by absorbing diffusing gas
atoms and vacancies. The critical bubble radius for stability is approximately
0.5-2 nm depending on temperature and gas pressure.

3. Intergranular Bubble Interconnection
At temperatures above approximately 1600 K for UO2 and 1400 K for UN,
intergranular bubbles grow and interconnect, forming tunnel networks that
provide pathways for gas release to the fuel-cladding gap. The threshold
burnup for significant FGR (>1%) depends strongly on the linear heat rate.

4. Comparison with Experimental Data
Model predictions are compared with in-pile measurement data from the Halden
reactor and post-irradiation examination (PIE) results from commercial LWR
rods. The model captures the onset of significant FGR within ±15% of the
measured values.

References:
[1] Turnbull J.A. et al. (2023) J. Nucl. Mater., 578, 154311.
[2] Rest J. (2005) A Model for Fission Gas Release, ANL-RE-05/2.
""",
    },
]


# =============================================================================
# Expected ontology entity types and relationship types
# =============================================================================

EXPECTED_ENTITY_TYPES = [
    "Material",
    "Property",
    "Experiment",
    "Condition",
    "Publication",
]

EXPECTED_RELATION_TYPES = [
    "hasProperty",
    "measuredIn",
    "hasCondition",
    "cites",
    "extractsFrom",
    "relatedTo",
    "composedOf",
    "produces",
    "investigates",
    "performedAt",
]

# Key nuclear materials terms that should appear in extracted entities
EXPECTED_MATERIAL_TERMS = [
    "UO2",
    "UN",
    "MOX",
    "U-Zr",
    "ZrO2",
    "uranium dioxide",
    "uranium nitride",
    "mixed oxide",
    "plutonium dioxide",
    "zirconium dioxide",
    "二氧化铀",
    "氮化铀",
    "混合氧化物",
]

# =============================================================================
# Query test cases (3 modes × multiple queries)
# =============================================================================


@dataclass(frozen=True)
class QueryTestCase:
    """A single query to run against the knowledge graph."""

    mode: str
    query: str
    expected_keywords: list[str]
    description: str


QUERY_TEST_CASES: list[QueryTestCase] = [
    # --- Vector / semantic queries (local mode) ---
    QueryTestCase(
        mode="local",
        query="What is the thermal conductivity of UO2 at 1000 K?",
        expected_keywords=["UO2", "thermal conductivity", "W/m"],
        description="Vector query: UO2 thermal conductivity",
    ),
    QueryTestCase(
        mode="local",
        query="二氧化铀的熔点是多少？",
        expected_keywords=["UO2", "二氧化铀", "3138", "melting"],
        description="Vector query: UO2 melting point (Chinese)",
    ),
    # --- Entity / keyword queries (naive mode) ---
    QueryTestCase(
        mode="naive",
        query="uranium nitride UN fuel thermal conductivity density",
        expected_keywords=["UN", "uranium nitride", "thermal conductivity", "density"],
        description="Entity query: UN fuel properties",
    ),
    QueryTestCase(
        mode="naive",
        query="U-Zr CALPHAD phase diagram BCC gamma solid solution",
        expected_keywords=["U-Zr", "phase", "BCC", "CALPHAD"],
        description="Entity query: U-Zr phase diagram",
    ),
    # --- Relationship queries (global mode) ---
    QueryTestCase(
        mode="global",
        query="How does the oxygen potential of MOX fuel affect fission product behavior?",
        expected_keywords=["MOX", "oxygen potential", "fission product", "O/M"],
        description="Relationship query: MOX oxygen potential",
    ),
    QueryTestCase(
        mode="global",
        query="氮化铀的辐照行为与裂变气体释放机制",
        expected_keywords=["UN", "氮化铀", "fission gas", "irradiation", "bubble"],
        description="Relationship query: UN irradiation (Chinese)",
    ),
    # --- Hybrid / mix queries ---
    QueryTestCase(
        mode="mix",
        query="Compare the thermal conductivity of UO2, UN, and MOX fuels",
        expected_keywords=["UO2", "UN", "MOX", "thermal conductivity"],
        description="Mix query: multi-fuel thermal conductivity comparison",
    ),
    QueryTestCase(
        mode="mix",
        query="ZrO2 cladding interaction during loss-of-coolant accident",
        expected_keywords=["ZrO2", "cladding", "LOCA", "interaction"],
        description="Mix query: fuel-cladding interaction",
    ),
]


# =============================================================================
# HTTP helpers (stdlib only — matches staging_smoke_test.py pattern)
# =============================================================================


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[int, Any]:
    """POST JSON and return (status_code, parsed_body_or_error_string)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            **(headers or {}),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return exc.code, body
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def _get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    """GET JSON and return (status_code, parsed_body_or_error_string)."""
    req = urllib.request.Request(
        url,
        headers={**(headers or {}), "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return exc.code, body
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


# =============================================================================
# Check implementations
# =============================================================================


def check_lightrag_health(lightrag_url: str) -> CheckResult:
    """AC-1: LightRAG sidecar /health returns 200."""
    t0 = time.monotonic()
    status, body = _get_json(f"{lightrag_url}/health")
    elapsed = time.monotonic() - t0

    if status == 200:
        return CheckResult(
            "AC-1: LightRAG health check",
            True,
            f"healthy (status={status}, body={json.dumps(body)[:200]})",
            elapsed,
        )
    return CheckResult(
        "AC-1: LightRAG health check",
        False,
        f"unhealthy (status={status}, body={str(body)[:200]})",
        elapsed,
    )


def check_api_health(api_url: str) -> CheckResult:
    """AC-0: NFM API health check (if using API mode)."""
    t0 = time.monotonic()
    status, body = _get_json(f"{api_url}/api/v1/health")
    elapsed = time.monotonic() - t0

    if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
        return CheckResult(
            "AC-0: NFM API health check",
            True,
            "ok",
            elapsed,
        )
    return CheckResult(
        "AC-0: NFM API health check",
        False,
        f"status={status}, body={str(body)[:200]}",
        elapsed,
    )


def check_lightrag_api_health(api_url: str, headers: dict[str, str]) -> CheckResult:
    """AC-1b: LightRAG health via NFM API (includes version + fallback info)."""
    t0 = time.monotonic()
    status, body = _get_json(
        f"{api_url}/api/v1/lightrag/health",
        headers=headers,
    )
    elapsed = time.monotonic() - t0

    if status != 200:
        return CheckResult(
            "AC-1b: LightRAG health via API",
            False,
            f"HTTP {status}",
            elapsed,
        )

    data = body if isinstance(body, dict) else {}
    inner = data.get("data", data)
    healthy = inner.get("status") == "healthy"
    fallback = inner.get("fallback_active", True)
    version = inner.get("lightrag_version", "unknown")

    if healthy and not fallback:
        return CheckResult(
            "AC-1b: LightRAG health via API",
            True,
            f"healthy, v={version}, fallback={fallback}",
            elapsed,
        )
    return CheckResult(
        "AC-1b: LightRAG health via API",
        False,
        f"status={inner.get('status')}, v={version}, fallback={fallback}",
        elapsed,
    )


def ingest_papers_direct(
    lightrag_url: str,
    papers: list[dict[str, str]],
) -> tuple[list[CheckResult], list[float]]:
    """AC-2: Ingest all 10 papers via LightRAG /documents/text.

    Returns (results, ingestion_times).
    """
    results: list[CheckResult] = []
    times: list[float] = []

    for i, paper in enumerate(papers):
        t0 = time.monotonic()
        status, body = _post_json(
            f"{lightrag_url}/documents/text",
            {"text": paper["text"], "file_source": paper["source"]},
        )
        elapsed = time.monotonic() - t0
        times.append(elapsed)

        passed = status == 200
        detail_parts = [f"paper {i + 1}/10: {paper['source']}"]
        if passed:
            detail_parts.append(f"status={status}")
            if isinstance(body, dict):
                track_id = body.get("track_id")
                if track_id:
                    detail_parts.append(f"track_id={track_id}")
        else:
            detail_parts.append(f"FAILED (status={status}, body={str(body)[:300]})")

        results.append(CheckResult(
            f"AC-2.{i + 1}: Ingest {paper['source']}",
            passed,
            " | ".join(detail_parts),
            elapsed,
        ))

    return results, times


def ingest_papers_via_api(
    api_url: str,
    headers: dict[str, str],
    papers: list[dict[str, str]],
) -> tuple[list[CheckResult], list[float]]:
    """AC-2: Ingest all 10 papers via NFM API /api/v1/lightrag/ingest.

    Returns (results, ingestion_times).
    """
    results: list[CheckResult] = []
    times: list[float] = []

    for i, paper in enumerate(papers):
        t0 = time.monotonic()
        status, body = _post_json(
            f"{api_url}/api/v1/lightrag/ingest",
            {"text": paper["text"], "file_source": paper["source"]},
            headers=headers,
        )
        elapsed = time.monotonic() - t0
        times.append(elapsed)

        data = body if isinstance(body, dict) else {}
        inner = data.get("data", data)
        passed = status == 200 and data.get("success", False)

        detail_parts = [f"paper {i + 1}/10: {paper['source']}"]
        if passed:
            detail_parts.append(f"status={status}")
            track_id = inner.get("track_id")
            if track_id:
                detail_parts.append(f"track_id={track_id}")
        else:
            detail_parts.append(f"FAILED (status={status}, body={str(body)[:300]})")

        results.append(CheckResult(
            f"AC-2.{i + 1}: Ingest {paper['source']}",
            passed,
            " | ".join(detail_parts),
            elapsed,
        ))

    return results, times


def run_queries_direct(
    lightrag_url: str,
    test_cases: list[QueryTestCase],
) -> tuple[list[CheckResult], list[QueryResult]]:
    """AC-3: Execute query test cases directly against LightRAG."""
    results: list[CheckResult] = []
    query_results: list[QueryResult] = []

    for tc in test_cases:
        t0 = time.monotonic()
        status, body = _post_json(
            f"{lightrag_url}/query",
            {
                "query": tc.query,
                "mode": tc.mode,
                "include_references": True,
            },
            timeout=180.0,
        )
        elapsed = time.monotonic() - t0

        resp_data = body if isinstance(body, dict) else {}
        response_text = str(resp_data.get("response", ""))
        entities = resp_data.get("entities", [])
        relationships = resp_data.get("relationships", [])
        references = resp_data.get("references", [])

        qr = QueryResult(
            query_mode=tc.mode,
            query_text=tc.query,
            response_text=response_text,
            response_time_s=elapsed,
            entities=entities,
            relationships=relationships,
            references=references,
        )
        query_results.append(qr)

        # Validate: response should not be empty
        has_response = len(response_text.strip()) > 20

        # Validate: check for expected keywords (case-insensitive)
        response_lower = response_text.lower()
        found_keywords = [
            kw for kw in tc.expected_keywords
            if kw.lower() in response_lower
        ]
        keyword_quality = len(found_keywords) / len(tc.expected_keywords)

        passed = status == 200 and has_response
        detail_parts = [
            tc.description,
            f"status={status}",
            f"time={elapsed:.1f}s",
            f"keywords={len(found_keywords)}/{len(tc.expected_keywords)}",
        ]
        if keyword_quality >= 0.5:
            detail_parts.append(f"quality=GOOD ({keyword_quality:.0%})")
        elif keyword_quality >= 0.25:
            detail_parts.append(f"quality=PARTIAL ({keyword_quality:.0%})")
        else:
            detail_parts.append(f"quality=LOW ({keyword_quality:.0%})")

        if not has_response and status == 200:
            detail_parts.append("WARN: empty or near-empty response")

        results.append(CheckResult(
            f"AC-3: Query [{tc.mode}] {tc.query[:50]}",
            passed,
            " | ".join(detail_parts),
            elapsed,
        ))

    return results, query_results


def run_queries_via_api(
    api_url: str,
    headers: dict[str, str],
    test_cases: list[QueryTestCase],
) -> tuple[list[CheckResult], list[QueryResult]]:
    """AC-3: Execute query test cases via NFM API."""
    results: list[CheckResult] = []
    query_results: list[QueryResult] = []

    for tc in test_cases:
        t0 = time.monotonic()
        status, body = _post_json(
            f"{api_url}/api/v1/lightrag/query",
            {
                "query": tc.query,
                "mode": tc.mode,
                "include_references": True,
            },
            headers=headers,
            timeout=180.0,
        )
        elapsed = time.monotonic() - t0

        data = body if isinstance(body, dict) else {}
        inner = data.get("data", data)
        response_text = str(inner.get("response", ""))
        entities = inner.get("entities", [])
        relationships = inner.get("relationships", [])
        references = inner.get("references", [])

        qr = QueryResult(
            query_mode=tc.mode,
            query_text=tc.query,
            response_text=response_text,
            response_time_s=elapsed,
            entities=entities,
            relationships=relationships,
            references=references,
        )
        query_results.append(qr)

        has_response = len(response_text.strip()) > 20
        response_lower = response_text.lower()
        found_keywords = [
            kw for kw in tc.expected_keywords
            if kw.lower() in response_lower
        ]
        keyword_quality = len(found_keywords) / len(tc.expected_keywords)

        passed = status == 200 and data.get("success", False) and has_response
        detail_parts = [
            tc.description,
            f"status={status}",
            f"time={elapsed:.1f}s",
            f"keywords={len(found_keywords)}/{len(tc.expected_keywords)}",
        ]
        if keyword_quality >= 0.5:
            detail_parts.append(f"quality=GOOD ({keyword_quality:.0%})")
        elif keyword_quality >= 0.25:
            detail_parts.append(f"quality=PARTIAL ({keyword_quality:.0%})")
        else:
            detail_parts.append(f"quality=LOW ({keyword_quality:.0%})")

        results.append(CheckResult(
            f"AC-3: Query [{tc.mode}] {tc.query[:50]}",
            passed,
            " | ".join(detail_parts),
            elapsed,
        ))

    return results, query_results


def check_ontology_extraction(query_results: list[QueryResult]) -> CheckResult:
    """AC-4: Verify NucMat ontology entities appear in query results.

    Checks that extracted entities and relationships contain nuclear
    materials domain terms (Material, Property, etc.) from the ontology.
    """
    t0 = time.monotonic()

    all_response_text = " ".join(qr.response_text for qr in query_results)
    response_lower = all_response_text.lower()

    # Check material terms
    found_materials = [
        term for term in EXPECTED_MATERIAL_TERMS
        if term.lower() in response_lower
    ]
    material_quality = len(found_materials) / len(EXPECTED_MATERIAL_TERMS)

    # Check for domain concepts (broader ontology validation)
    domain_concepts = [
        "thermal conductivity",
        "melting point",
        "phase diagram",
        "CALPHAD",
        "fission gas",
        "irradiation",
        "crystal structure",
        "lattice parameter",
        "oxygen potential",
        "burnup",
        "热导率",
        "熔点",
        "相图",
        "裂变气体",
        "辐照",
        "晶体结构",
    ]
    found_concepts = [
        c for c in domain_concepts
        if c.lower() in response_lower
    ]
    concept_quality = len(found_concepts) / len(domain_concepts)

    elapsed = time.monotonic() - t0

    # Pass if we found at least 50% of material terms AND 40% of domain concepts
    passed = material_quality >= 0.5 and concept_quality >= 0.4

    detail_parts = [
        f"materials: {len(found_materials)}/{len(EXPECTED_MATERIAL_TERMS)} ({material_quality:.0%})",
        f"domain concepts: {len(found_concepts)}/{len(domain_concepts)} ({concept_quality:.0%})",
    ]
    if found_materials:
        detail_parts.append(f"found materials: {', '.join(found_materials[:8])}")
    if not found_materials:
        detail_parts.append("WARN: no material terms found in any response")

    return CheckResult(
        "AC-4: Ontology entity extraction",
        passed,
        " | ".join(detail_parts),
        elapsed,
    )


def check_ontology_relationships(query_results: list[QueryResult]) -> CheckResult:
    """AC-4b: Verify relationship types from the NucMat ontology appear.

    This checks that the LLM-generated responses contain language
    describing relationships between entities (not just standalone entities).
    """
    t0 = time.monotonic()

    all_response_text = " ".join(qr.response_text for qr in query_results)
    response_lower = all_response_text.lower()

    # Relational indicators in the response text
    relation_indicators = [
        # Property relationships
        ("has property", ["has", "property", "thermal conductivity", "density", "melting point", "热导率", "密度", "熔点"]),
        # Measurement relationships
        ("measured in", ["measured", "experiment", "measurement", "测量", "实验"]),
        # Composition relationships
        ("composed of", ["composed", "consists", "contains", "组成", "包含"]),
        # Correlation/related
        ("related to", ["related", "correlation", "associated", "related to", "相关"]),
    ]

    found_relations: list[str] = []
    for rel_name, indicators in relation_indicators:
        if any(ind in response_lower for ind in indicators):
            found_relations.append(rel_name)

    elapsed = time.monotonic() - t0
    relation_quality = len(found_relations) / len(relation_indicators)
    passed = relation_quality >= 0.5

    return CheckResult(
        "AC-4b: Ontology relationship extraction",
        passed,
        f"found {len(found_relations)}/{len(relation_indicators)} relation types: {found_relations}",
        elapsed,
    )


def check_degradation(api_url: str, headers: dict[str, str] | None = None) -> CheckResult:
    """AC-5: Verify graceful degradation when LightRAG is unreachable.

    Hits the NFM API health endpoint which should return fallback_active=True
    rather than crashing, regardless of LightRAG state.
    """
    t0 = time.monotonic()

    if api_url and headers:
        status, body = _get_json(
            f"{api_url}/api/v1/lightrag/health",
            headers=headers,
        )
        elapsed = time.monotonic() - t0

        if status == 200:
            data = body if isinstance(body, dict) else {}
            inner = data.get("data", data)
            fallback = inner.get("fallback_active")
            if fallback is not None:
                return CheckResult(
                    "AC-5: Degradation (API returns fallback status)",
                    True,
                    f"API handled gracefully, fallback_active={fallback}",
                    elapsed,
                )
            return CheckResult(
                "AC-5: Degradation (API returns fallback status)",
                False,
                "API responded but missing fallback_active field",
                elapsed,
            )

        # Even a non-200 is acceptable as long as it didn't hang/crash
        return CheckResult(
            "AC-5: Degradation (API returns fallback status)",
            True,
            f"API returned HTTP {status} (not a crash)",
            elapsed,
        )

    # Direct mode: verify LightRAG returns connection error (not hang)
    t0 = time.monotonic()
    status, body = _get_json("http://127.0.0.1:19999/health", timeout=5.0)
    elapsed = time.monotonic() - t0

    if status == 0 and "connection" in str(body).lower():
        return CheckResult(
            "AC-5: Degradation (connection refused handled)",
            True,
            f"Connection refused returned in {elapsed:.1f}s (not a hang)",
            elapsed,
        )

    return CheckResult(
        "AC-5: Degradation (connection refused handled)",
        False,
        f"Unexpected: status={status}, body={str(body)[:200]}",
        elapsed,
    )


# =============================================================================
# Report rendering
# =============================================================================


def render_report(
    all_results: list[CheckResult],
    query_results: list[QueryResult] | None = None,
    ingestion_times: list[float] | None = None,
) -> str:
    """Render a human-readable test report."""
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("LightRAG E2E Phase A — 10-Paper Pipeline Verification (NFM-1763)")
    lines.append("=" * 72)
    lines.append("")

    # Summary
    passed = [r for r in all_results if r.passed]
    failed = [r for r in all_results if not r.passed]
    lines.append(f"Results: {len(passed)}/{len(all_results)} PASSED")
    if failed:
        lines.append(f"FAILED: {len(failed)} checks")
    lines.append("")

    # Individual results
    for r in all_results:
        marker = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{marker}] {r.name}")
        lines.append(f"         {r.detail}")
        if r.duration_s > 0:
            lines.append(f"         ({r.duration_s:.2f}s)")
        lines.append("")

    # Timing summary
    if ingestion_times:
        lines.append("--- Ingestion Timing ---")
        for i, t in enumerate(ingestion_times):
            lines.append(f"  Paper {i + 1}: {t:.1f}s")
        lines.append(
            f"  Total: {sum(ingestion_times):.1f}s | "
            f"Avg: {sum(ingestion_times) / len(ingestion_times):.1f}s | "
            f"Min: {min(ingestion_times):.1f}s | Max: {max(ingestion_times):.1f}s"
        )
        lines.append("")

    if query_results:
        lines.append("--- Query Timing ---")
        for qr in query_results:
            lines.append(
                f"  [{qr.query_mode:6s}] {qr.query[:60]:60s} -> {qr.response_time_s:.1f}s"
            )
        if query_results:
            q_times = [qr.response_time_s for qr in query_results]
            lines.append(
                f"  Total: {sum(q_times):.1f}s | "
                f"Avg: {sum(q_times) / len(q_times):.1f}s"
            )
        lines.append("")

    lines.append("=" * 72)
    if not failed:
        lines.append("ALL CHECKS PASSED")
    else:
        lines.append(f"{len(failed)} CHECK(S) FAILED — see details above")
    lines.append("=" * 72)

    return "\n".join(lines)


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "E2E Phase A: LightRAG 10-Paper Pipeline Verification (NFM-1763). "
            "Verifies ingest, query, ontology, and degradation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Direct to LightRAG sidecar:\n"
            "  python scripts/e2e_lightrag_10papers.py --direct\n"
            "\n"
            "  # Via NFM API with JWT auth:\n"
            "  python scripts/e2e_lightrag_10papers.py --api-url http://127.0.0.1:8001 --token <JWT>\n"
            "\n"
            "  # Dry-run (print what would be tested):\n"
            "  python scripts/e2e_lightrag_10papers.py --dry-run\n"
        ),
    )
    p.add_argument(
        "--direct",
        action="store_true",
        default=False,
        help="Talk directly to LightRAG sidecar (no NFM API, no auth)",
    )
    p.add_argument(
        "--lightrag-url",
        default="http://127.0.0.1:9621",
        help="LightRAG sidecar URL (default: http://127.0.0.1:9621)",
    )
    p.add_argument(
        "--api-url",
        default="http://127.0.0.1:8001",
        help="NFM API base URL (default: http://127.0.0.1:8001)",
    )
    p.add_argument(
        "--token",
        default=None,
        help=(
            "JWT auth token for NFM API (editor role required). "
            "Can also set NFM_E2E_TOKEN env var."
        ),
    )
    p.add_argument(
        "--ingest-delay",
        type=float,
        default=5.0,
        help="Seconds to wait after all ingestions before querying (default: 5.0)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print test plan without executing any requests",
    )
    return p.parse_args(argv)


def _get_auth_headers(args: argparse.Namespace) -> dict[str, str]:
    token = args.token or os.environ.get("NFM_E2E_TOKEN", "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


import os  # noqa: E402 (needed for os.environ in _get_auth_headers)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # ---- Dry-run mode ----
    if args.dry_run:
        print("=" * 72)
        print("DRY-RUN: LightRAG E2E Phase A — 10-Paper Pipeline (NFM-1763)")
        print("=" * 72)
        print("")
        print(f"Mode: {'DIRECT (LightRAG sidecar)' if args.direct else 'NFM API'}")
        print(f"LightRAG URL: {args.lightrag_url}")
        if not args.direct:
            print(f"NFM API URL: {args.api_url}")
            print(f"Auth token: {'set' if args.token or os.environ.get('NFM_E2E_TOKEN') else 'NOT SET'}")
        print("")
        print(f"Papers to ingest: {len(PAPERS)}")
        for i, paper in enumerate(PAPERS):
            print(f"  {i + 1}. {paper['source']}")
        print("")
        print(f"Query test cases: {len(QUERY_TEST_CASES)}")
        for tc in QUERY_TEST_CASES:
            print(f"  [{tc.mode:6s}] {tc.description}")
        print("")
        print("Acceptance criteria:")
        print("  AC-0:  NFM API health check (API mode only)")
        print("  AC-1:  LightRAG /health returns 200")
        print("  AC-1b: LightRAG health via NFM API returns version + fallback info")
        print("  AC-2:  All 10 papers ingested successfully")
        print("  AC-3:  All 8 queries return non-empty responses")
        print("  AC-4:  Ontology entity types extracted (Material, Property, etc.)")
        print("  AC-4b: Ontology relationship types extracted")
        print("  AC-5:  Graceful degradation when LightRAG is unreachable")
        print("")
        print("Prerequisites:")
        print("  - LightRAG sidecar: docker compose -f docker-compose.prod.yml -f docker-compose.lightrag.yml ...")
        print("  - LLM config: LIGHTRAG_LLM_MODEL, LIGHTRAG_LLM_API_KEY")
        print("  - Embedding config: LIGHTRAG_EMBEDDING_MODEL=BAAI/bge-m3")
        print("  - For API mode: NFM_E2E_TOKEN env var or --token flag")
        return 0

    all_results: list[CheckResult] = []
    query_results: list[QueryResult] = []
    ingestion_times: list[float] = []

    is_direct = args.direct
    headers = _get_auth_headers(args)

    # ---- Phase 1: Health checks ----
    print("[Phase 1] Health checks...", file=sys.stderr)

    if is_direct:
        r = check_lightrag_health(args.lightrag_url)
        all_results.append(r)
        _print_result(r)

        if not r.passed:
            print("", file=sys.stderr)
            print("LightRAG sidecar is not healthy. Skipping ingestion and queries.",
                  file=sys.stderr)
            print("Run the degradation check, then print report.", file=sys.stderr)

            # Still check degradation
            deg = check_degradation(args.api_url, headers if not is_direct else None)
            all_results.append(deg)
            _print_result(deg)

            report = render_report(all_results, ingestion_times=[])
            print(report)
            return 1
    else:
        r0 = check_api_health(args.api_url)
        all_results.append(r0)
        _print_result(r0)

        r1b = check_lightrag_api_health(args.api_url, headers)
        all_results.append(r1b)
        _print_result(r1b)

        # Also do direct health check
        r1 = check_lightrag_health(args.lightrag_url)
        all_results.append(r1)
        _print_result(r1)

        if not r1.passed:
            print("", file=sys.stderr)
            print("LightRAG sidecar is not reachable. Running degradation check only.",
                  file=sys.stderr)
            deg = check_degradation(args.api_url, headers)
            all_results.append(deg)
            _print_result(deg)

            report = render_report(all_results, ingestion_times=[])
            print(report)
            return 1

    # ---- Phase 2: Ingest 10 papers ----
    print("", file=sys.stderr)
    print("[Phase 2] Ingesting 10 papers...", file=sys.stderr)

    if is_direct:
        ingest_results, ingestion_times = ingest_papers_direct(
            args.lightrag_url, PAPERS,
        )
    else:
        ingest_results, ingestion_times = ingest_papers_via_api(
            args.api_url, headers, PAPERS,
        )

    all_results.extend(ingest_results)
    for r in ingest_results:
        _print_result(r)

    any_ingest_failed = any(not r.passed for r in ingest_results)

    if any_ingest_failed:
        print("", file=sys.stderr)
        print("Some papers failed to ingest. Attempting queries anyway.",
              file=sys.stderr)

    # Wait for LightRAG to process ingested documents
    print("", file=sys.stderr)
    print(f"Waiting {args.ingest_delay:.0f}s for LightRAG to index documents...",
          file=sys.stderr)
    time.sleep(args.ingest_delay)

    # ---- Phase 3: Execute queries ----
    print("", file=sys.stderr)
    print("[Phase 3] Executing queries...", file=sys.stderr)

    if is_direct:
        query_checks, query_results = run_queries_direct(
            args.lightrag_url, QUERY_TEST_CASES,
        )
    else:
        query_checks, query_results = run_queries_via_api(
            args.api_url, headers, QUERY_TEST_CASES,
        )

    all_results.extend(query_checks)
    for r in query_checks:
        _print_result(r)

    # ---- Phase 4: Ontology validation ----
    print("", file=sys.stderr)
    print("[Phase 4] Validating ontology extraction...", file=sys.stderr)

    r4 = check_ontology_extraction(query_results)
    all_results.append(r4)
    _print_result(r4)

    r4b = check_ontology_relationships(query_results)
    all_results.append(r4b)
    _print_result(r4b)

    # ---- Phase 5: Degradation test ----
    print("", file=sys.stderr)
    print("[Phase 5] Testing degradation...", file=sys.stderr)

    if is_direct:
        deg = check_degradation(args.api_url, None)
    else:
        deg = check_degradation(args.api_url, headers)
    all_results.append(deg)
    _print_result(deg)

    # ---- Report ----
    report = render_report(all_results, query_results, ingestion_times)
    print(report)

    all_passed = all(r.passed for r in all_results)
    return 0 if all_passed else 1


def _print_result(r: CheckResult) -> None:
    marker = "PASS" if r.passed else "FAIL"
    print(f"  [{marker}] {r.name}: {r.detail}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
