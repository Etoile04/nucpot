---
name: literature-search
description: Literature search and source validation for nuclear materials: queries NIST IPR, OpenKIM, Materials Project APIs; performs credibility scoring; extracts uncertainty ranges from experimental and computational sources. Use when validating reference values, finding LAMMPS potentials, or assessing source quality.
---

<objective>
Provides automated literature search and source validation for nuclear materials data, including credibility assessment and uncertainty extraction from experimental and computational sources.
</objective>

<data_sources>
Primary data sources for nuclear materials literature:
- **NIST IPR** (Interatomic Potentials Repository) - LAMMPS potentials, parameter files
- **OpenKIM** (Open Knowledgebase of Interatomic Models) - Verified potentials, benchmarks
- **Materials Project** - DFT calculations, formation energies, elastic properties
- **ICSD** - Experimental crystal structures (requires subscription)
- **Literature databases** - Experimental property measurements with uncertainty

For API access details, see:
- [references/api-endpoints.md](references/api-endpoints.md) - API usage and authentication
- [references/credibility-scoring.md](references/credibility-scoring.md) - Source quality assessment
- [references/uncertainty-extraction.md](references/uncertainty-extraction.md) - Uncertainty quantification methods
</data_sources>

<workflow>
When performing literature search for nuclear materials:

1. **Identify search parameters**
   - Material: U, UO₂, Zr, Fe, U-Zr, or specific alloy
   - Property: Lattice constant, elastic moduli, thermal expansion, etc.
   - Temperature range: Operating conditions
   - Source type: Experimental, DFT, or potential parameters

2. **Query primary databases**
   - Start with [references/api-endpoints.md](references/api-endpoints.md)
   - Query NIST IPR for potentials
   - Query OpenKIM for verified models
   - Query Materials Project for DFT data
   - Search general literature for experimental values

3. **Assess source credibility**
   - Use [references/credibility-scoring.md](references/credibility-scoring.md)
   - Assign credibility score (0-100)
   - Classify as Tier 1-4 source
   - Note methodological limitations

4. **Extract uncertainty**
   - Use [references/uncertainty-extraction.md](references/uncertainty-extraction.md)
   - Extract experimental uncertainty (if reported)
   - Estimate computational uncertainty (method-dependent)
   - Propagate uncertainty to final value

5. **Resolve conflicts**
   - Apply credibility hierarchy: Tier 1 > Tier 2 > Tier 3 > Tier 4
   - Prefer experimental > DFT when both available
   - Prefer recent > historical sources
   - Document rationale for selection

6. **Return validated reference**
   - Material property with value ± uncertainty
   - Source credibility score
   - Confidence score (70%+ required for autonomous use)
   - Full citation and DOI
</workflow>

<credibility_hierarchy>
**Tier 1 (Score: 90-100)**: Gold standard sources
- NIST IPR reviewed potentials
- OpenKIM verified models with benchmarks
- High-precision experimental measurements (<1% uncertainty)
- Peer-reviewed primary literature with validation

**Tier 2 (Score: 70-89)**: Reliable sources
- Materials Project DFT (standard DFT accuracy)
- NIST IPR unreviewed potentials
- Peer-reviewed experimental literature (1-5% uncertainty)
- Established computational methods

**Tier 3 (Score: 50-69)**: Moderate sources
- Preprint or non-peer-reviewed computational results
- Experimental data with limited documentation
- DFT with basic functional (PBE) for complex systems
- Literature with indirect measurement

**Tier 4 (Score: 0-49)**: Low credibility
- Unvalidated computational results
- Poorly documented experimental data
- Secondary/tertiary sources (citing primary data)
- Unverified claims without uncertainty

**Decision Rules**:
- Prefer Tier 1 sources over all others
- Use Tier 2 when Tier 1 unavailable
- Tier 3 requires additional validation or uncertainty padding
- Tier 4 only acceptable for exploratory analysis
- Mark as "escalate required" when best source < Tier 2
</credibility_hierarchy>

<uncertainty_types>
**Experimental Uncertainty**:
- Direct measurement error (reported in paper)
- Systematic errors from calibration
- Sample purity effects
- Temperature/pressure control uncertainty
- Typical range: 0.1-5% depending on measurement

**Computational Uncertainty**:
- DFT functional error (systematic, ~5-10%)
- Basis set incompleteness (~1-3%)
- Convergence criteria tolerance
- k-point sampling error (~1-2%)
- Typical range: 5-15% total for standard DFT

**Potential Parameter Uncertainty**:
- Fitting range extrapolation
- Training data coverage gaps
- Temperature transferability
- Composition transferability (alloys)
- Typical range: 10-30% outside fitted range

**Combined Uncertainty** (when multiple sources):
- Root-mean-square of component uncertainties
- σ_total = √(σ₁² + σ₂² + ... + σₙ²)
- Add systematic errors linearly
</uncertainty_types>

<output_format>
Structure literature search results as:

**Material**: [name, phase, composition]
**Property**: [lattice constant / elastic modulus / thermal expansion]
**Value**: [numerical value with units]
**Uncertainty**: [± range or percentage]
**Source**: [database / journal / DOI]
**Credibility Score**: [0-100, Tier level]
**Confidence Score**: [0-100, based on credibility + uncertainty]
**Recommendation**: [Use / Use with caution / Escalate required]

For conflicts:
**Primary Value**: [selected value with source]
**Alternative Values**: [other sources considered]
**Rationale**: [why primary value selected]
**Remaining Concerns**: [if any, what needs verification]
</output_format>

<validation>
A complete literature search response includes:
- Searched all relevant databases (NIST IPR, OpenKIM, Materials Project)
- Credibility score assigned with tier classification
- Uncertainty extracted or estimated with methodology
- Clear primary value selection with documented rationale
- Confidence score ≥70% for autonomous use, <70% triggers escalation
- Full citation with DOI or persistent identifier

Never:
- Accept single source without credibility assessment
- Use Tier 4 sources without escalation
- Ignore reported experimental uncertainty
- Skip uncertainty estimation for computational results
- Select conflicting values without documentation
- Use sources outside calibrated temperature range
</validation>

<integration_notes>
This skill works with other nuclear domain skills:
- **nuclear-materials-knowledge** - Validates found potentials against known parameters
- **lammps-debugger** - Provides literature context for failure analysis
- **quality-audit** - Supplies validated reference values for audit checks
- **template-selector** - Delivers validated parameters for LAMMPS input

Common workflow: literature-search finds value → nuclear-materials-knowledge validates → quality-audit checks compliance → template-selector generates input
</integration_notes>

<special_considerations>
**Temperature Range Matching**:
- Always verify source calibration temperature
- Flag if simulation T outside source T range
- Recommend alternative sources or note extrapolation risk
- Apply uncertainty penalty for extrapolation

**Stoichiometry Effects**:
- UO₂±x: Lattice parameter varies with oxygen content
- Note exact composition in source
- Adjust uncertainty for composition dependence
- Prefer stoichiometric measurements when available

**Phase Identification**:
- Verify source phase matches simulation phase
- Note phase transition temperatures
- Flag mismatches between source and simulation
- Use phase-appropriate lattice parameters

**Citation Completeness**:
- Always include DOI when available
- Include publication year for recency assessment
- Note measurement/computation method
- Include sample details (purity, conditions)
</special_considerations>
