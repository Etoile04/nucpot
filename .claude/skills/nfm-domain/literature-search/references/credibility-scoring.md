# Source Credibility Scoring for Nuclear Materials Literature

## Overview

Credibility scoring assigns a 0-100 score to each source based on methodological rigor, peer review, validation, and uncertainty reporting. Scores map to Tier 1-4 classification for source selection decisions.

## Scoring Dimensions

### 1. Methodological Rigor (0-30 points)

**Experimental Methods (30 points)**:
- 30 pts: High-precision measurements (<1% uncertainty), controlled conditions
- 25 pts: Standard measurements (1-5% uncertainty), well-documented setup
- 20 pts: Basic measurements (5-10% uncertainty), adequate documentation
- 15 pts: Measurements with limited documentation (10-20% uncertainty)
- 10 pts: Poorly documented measurements (>20% uncertainty)
- 0 pts: Methodology not described

**Computational Methods (30 points)**:
- 30 pts: High-level theory (QMC, RPA) with systematic error analysis
- 25 pts: DFT with hybrid functional + GW, benchmarked against experiment
- 20 pts: Standard DFT (PBE, GGA) with convergence testing
- 15 pts: Basic DFT without convergence data
- 10 pts: Semi-empirical or force fitting only
- 0 pts: Method unspecified

**Potential Development (30 points)**:
- 30 pts: Fitted to extensive dataset (properties + temperatures), validated
- 25 pts: Fitted to multiple properties, some validation
- 20 pts: Fitted to single property, limited validation
- 15 pts: Parameter transfer from similar system
- 10 pts: Generic parameters without fitting
- 0 pts: Method unspecified

### 2. Peer Review Status (0-20 points)

- 20 pts: Peer-reviewed journal with rigorous standards (Nature, Science, PRL, PRB, Acta Materialia)
- 18 pts: Peer-reviewed journal with moderate standards
- 15 pts: Conference proceedings with review
- 12 pts: Preprint server (arXiv) with later peer review
- 8 pts: Preprint server (arXiv) without peer review
- 5 pts: Institutional report (NIST, LLNL, LANL technical reports)
- 3 pts: Thesis or dissertation
- 0 pts: Unpublished, no external review

### 3. Validation and Verification (0-25 points)

**Experimental Validation (25 points)**:
- 25 pts: Cross-lab validation, intercomparison shows agreement
- 20 pts: Standard reference materials used, traceable calibration
- 15 pts: Internal validation, reasonable consistency
- 10 pts: Limited validation, comparison to other work
- 5 pts: No validation, but methods appear sound
- 0 pts: No validation mentioned

**Computational Validation (25 points)**:
- 25 pts: Benchmark against experiment (<2% error), cross-method validation
- 20 pts: Compared to experiment (2-5% error), some cross-validation
- 15 pts: Compared to other computational results, no experimental validation
- 10 pts: Internal validation (convergence testing) only
- 5 pts: No validation reported
- 0 pts: Cannot assess validation

**Potential Validation (25 points)**:
- 25 pts: Extensive testing (multiple properties, temperatures), published benchmarks
- 20 pts: Multiple property testing, some benchmarks
- 15 pts: Single property validation (lattice constant only)
- 10 pts: Tested against few configurations
- 5 pts: Validation promised but not shown
- 0 pts: No validation

### 4. Uncertainty Quantification (0-15 points)

- 15 pts: Full uncertainty analysis (systematic + random error, propagation)
- 12 pts: Uncertainty reported for all measured values
- 10 pts: Uncertainty reported for key values
- 7 pts: Estimated uncertainty given
- 5 pts: Qualitative uncertainty discussion
- 3 pts: Error bars in figures (no numeric values)
- 0 pts: No uncertainty discussion

### 5. Recency and Relevance (0-10 points)

**Year Weighting**:
- 10 pts: Published within last 5 years (2021-2026)
- 8 pts: Published 5-10 years ago (2016-2020)
- 6 pts: Published 10-15 years ago (2011-2015)
- 4 pts: Published 15-20 years ago (2006-2010)
- 2 pts: Published >20 years ago
- 0 pts: No date available

**Relevance Bonus**:
- +3 pts: Direct measurement of exact material/property
- +2 pts: Measurement of closely related system
- +1 pts: General methodology applicable
- 0 pts: Tangential relevance

### 6. Data Completeness (0-10 points)

- 10 pts: Complete reporting (materials, conditions, methods, raw data)
- 8 pts: Most information reported, minor gaps
- 6 pts: Adequate reporting for use
- 4 pts: Incomplete reporting, some assumptions needed
- 2 pts: Minimal reporting, significant assumptions
- 0 pts: Inadequate reporting

## Score to Tier Mapping

**Tier 1 (90-100 points)**: Gold standard
- Minimum requirements: ≥25 methodological, ≥15 peer review, ≥20 validation
- Typical profile: Peer-reviewed, high-precision measurement, full validation, recent

**Tier 2 (70-89 points)**: Reliable source
- Minimum requirements: ≥20 methodological, ≥12 peer review, ≥15 validation
- Typical profile: Peer-reviewed, standard measurement, some validation

**Tier 3 (50-69 points)**: Moderate credibility
- Minimum requirements: ≥15 methodological, ≥8 peer review, ≥10 validation
- Typical profile: Some peer review, basic validation, limited uncertainty

**Tier 4 (0-49 points)**: Low credibility
- Does not meet Tier 3 minimum requirements
- Use only for exploratory analysis, requires escalation

## Scoring Examples

### Example 1: High-Quality Experimental (Tier 1)
- **Source**: Nature Materials paper on UO₂ lattice expansion (2023)
- **Method**: High-precision XRD with controlled atmosphere (30/30)
- **Peer Review**: Nature Materials (20/20)
- **Validation**: Cross-lab comparison, NIST traceable (25/25)
- **Uncertainty**: Full analysis, ±0.001 Å (15/15)
- **Recency**: 2023 publication (10/10)
- **Completeness**: Full methods, raw data available (10/10)
- **Total**: 110/110 → cap at 100/100 (Tier 1)

### Example 2: Standard DFT Calculation (Tier 2)
- **Source**: PRB paper on U elastic constants (2018)
- **Method**: DFT with PAW potential, convergence testing (20/30)
- **Peer Review**: Physical Review B (18/20)
- **Validation**: Compared to experiment, reasonable agreement (20/25)
- **Uncertainty**: DFT functional error noted (7/15)
- **Recency**: 2018 (6/10)
- **Completeness**: Methods well-documented (8/10)
- **Total**: 79/100 (Tier 2)

### Example 3: Preprint DFT (Tier 3)
- **Source**: arXiv preprint on U-Zr phase diagram (2024)
- **Method**: Basic DFT (PBE), limited convergence (15/30)
- **Peer Review**: arXiv only (8/20)
- **Validation**: Compared to other DFT, no experiment (15/25)
- **Uncertainty**: Estimated DFT error (7/15)
- **Recency**: 2024 (10/10)
- **Completeness**: Some methods not described (6/10)
- **Total**: 61/100 (Tier 3)

### Example 4: Old Potential (Tier 3)
- **Source**: 2005 paper on Fe EAM potential
- **Method**: Fitted to multiple properties (20/30)
- **Peer Review**: Published journal (18/20)
- **Validation**: Single property validation (15/25)
- **Uncertainty**: Not reported (3/15)
- **Recency**: 2005 (4/10)
- **Completeness**: Methods documented (8/10)
- **Total**: 68/100 (Tier 3)

### Example 5: Unvalidated Calculation (Tier 4)
- **Source**: Conference abstract with UO₂ calculation
- **Method**: Unspecified DFT method (10/30)
- **Peer Review**: Conference abstract (15/20)
- **Validation**: None (0/25)
- **Uncertainty**: Not mentioned (0/15)
- **Recency**: Recent (10/10)
- **Completeness**: Minimal (2/10)
- **Total**: 37/100 (Tier 4)

## Decision Rules

### Autonomous Use (Confidence ≥70%)
- **Required**: Tier 1 or Tier 2
- **Required**: Uncertainty ≤5% (experimental) or ≤15% (computational)
- **Required**: Validation against experimental data (for computational)
- **Action**: Use directly without escalation

### Cautious Use (Confidence 50-69%)
- **Allowed**: Tier 3 with documented limitations
- **Required**: Uncertainty quantification (even if estimated)
- **Required**: Cross-check with other sources
- **Action**: Use with additional uncertainty padding, note limitations

### Escalation Required (Confidence <50%)
- **Trigger**: Tier 4 source, or Tier 3 without validation
- **Action**: Require human review before use
- **Outcome**: May still be usable with proper justification

### Multiple Sources
- **Strategy**: Use weighted average with credibility as weight
- **Formula**: value = Σ(wᵢ × vᵢ) / Σwᵢ where wᵢ = credibility_scoreᵢ
- **Alternative**: Select highest-credibility source, note others
- **Conflict**: If values disagree >10% and similar credibility, escalate

## Special Considerations

### NIST IPR Potentials
- **Base Score**: Tier 2 (70-89) by default
- **Upgrade to Tier 1** if:
  - Published in peer-reviewed journal
  - Extensive validation data available
  - Recent publication (<10 years)
- **Downgrade to Tier 3** if:
  - Limited or no validation
  - Old potential (>20 years)
  - Fitted to limited dataset

### OpenKIM Models
- **Base Score**: Depends on verification status
- **Verified with benchmarks**: Tier 1 (90+)
- **Basic verification**: Tier 2 (70-89)
- **No verification**: Tier 3 (50-69)
- **Check**: Verification results page for each model

### Materials Project DFT
- **Base Score**: Tier 2 (70-89) for standard calculations
- **Limitations**:
  - DFT functional error (~5-10% systematic)
  - Temperature = 0 K (unless noted)
  - Perfect crystal assumed
- **Use for**: Lattice constants, formation energies, elastic moduli
- **Avoid for**: High-temperature properties, defect energies without correction

### Literature Values
- **Experimental**:
  - High-precision (<1%): Tier 1
  - Standard (1-5%): Tier 2
  - Poorly characterized (>5%): Tier 3
- **Computational**:
  - High-level theory: Tier 1
  - Standard DFT: Tier 2
  - Basic DFT: Tier 3

## Uncertainty Padding

### When Using Tier 3 Sources
- **Add 50% to reported uncertainty**
- **Example**: If source reports ±0.01 Å, use ±0.015 Å
- **Rationale**: Account for lower validation

### When Using Computational Sources
- **Add systematic DFT error** (5-10%) to reported uncertainty
- **Example**: ±1% from calculation → ±6-11% total
- **Check**: Does potential include DFT error? If not, add it.

### When Extrapolating
- **For temperature extrapolation**: Add 2% per 100 K
- **For composition extrapolation**: Add 5-10%
- **Rationale**: Potentials unreliable outside fitted range

## Red Flags

### Automatic Downgrade to Tier 4
- **No peer review**: Thesis, report, webpage only
- **No methods described**: Cannot assess quality
- **No uncertainty mentioned**: Cannot assess reliability
- **Unrealistic precision**: <0.1% without rigorous justification
- **Contradicts established data**: Without strong validation

### Require Escalation
- **Single source**: Only one measurement available
- **Large discrepancy**: >10% difference from other sources
- **Unusual methodology**: Novel technique without validation
- **Outlier**: Value far from established range
- **Conflict**: High-credibility sources disagree

## Scoring Practice

### Quick Scoring (5-10 seconds)
1. Check peer review (journal/preprint)
2. Check publication year
3. Check if uncertainty reported
4. Assign approximate tier
5. Use for routine decisions

### Full Scoring (1-2 minutes)
1. Evaluate all 6 dimensions
2. Assign points to each
3. Map to tier
4. Note limitations
5. Use for critical decisions (conflict, unusual values)

### Team Consensus (When Disputed)
1. Have multiple people score independently
2. Compare scores, discuss differences
3. Revise scores if needed
4. Document consensus reasoning
5. Use for controversial sources

## Documentation Template

```
Source ID: [identifier]
Citation: [full citation]
Score: [0-100]
Tier: [1-4]

Scoring Breakdown:
- Methodological Rigor: [0-30]
- Peer Review: [0-20]
- Validation: [0-25]
- Uncertainty: [0-15]
- Recency: [0-10]
- Completeness: [0-10]

Decision: [Use / Use with caution / Escalate]
Confidence: [0-100%]
Notes: [specific concerns or limitations]
```
