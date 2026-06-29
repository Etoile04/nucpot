# Uncertainty Extraction and Quantification

## Overview

Uncertainty extraction identifies and quantifies errors from experimental measurements, computational methods, and potential parameter fitting. All reference values must include uncertainty estimates for autonomous use by the nuclear-domain-expert agent.

## Types of Uncertainty

### 1. Experimental Uncertainty

#### Random Error (Statistical)
- **Source**: Measurement repeatability, sample variation
- **Characterization**: Standard deviation, standard error
- **Extraction**: Usually reported directly in paper
- **Typical magnitude**: 0.1-1% of measured value
- **Example**: "Lattice constant: 5.470 ± 0.005 Å" (±0.1%)

#### Systematic Error (Bias)
- **Source**: Calibration errors, instrument drift, method limitations
- **Characterization**: Bias estimate, correction uncertainty
- **Extraction**: Look in methods section, calibration discussion
- **Typical magnitude**: 0.5-5% of measured value
- **Example**: Temperature sensor calibration ±0.5 K affects lattice constant

#### Sample Uncertainty
- **Source**: Purity, stoichiometry, defect concentration
- **Characterization**: Composition analysis uncertainty
- **Extraction**: Sample characterization section
- **Typical magnitude**: 0.1-2% per at% impurity
- **Example**: UO₂.00 vs UO₁.₉₈ changes lattice constant by ~0.2%

### 2. Computational Uncertainty

#### DFT Functional Error (Systematic)
- **Source**: Approximate exchange-correlation functional
- **Typical magnitude**:
  - LDA/GGA: 5-10% (lattice constants typically 1-2% error)
  - Hybrid: 2-5% (better, but not perfect)
  - QMC/RPA: <2% (near-exact, expensive)
- **Extraction**: Compare to experiment or higher-level theory
- **Default assumption**: ±5% for standard GGA (PBE)

#### Basis Set Incompleteness
- **Source**: Finite plane-wave cutoff, limited k-points
- **Typical magnitude**: 1-3% (if convergence testing shown)
- **Extraction**: Check convergence plots in paper
- **Assumption if not reported**: ±2%

#### Finite-Size Effects
- **Source**: Limited supercell size, k-point sampling
- **Typical magnitude**: 1-5% (depending on property)
- **Extraction**: Check cell size, k-point mesh
- **Assumption if not reported**: ±2%

#### Total DFT Uncertainty
- **Combine**: Root-mean-square of components
- **Formula**: σ_total = √(σ_functional² + σ_basis² + σ_finite²)
- **Typical**: ±5-10% for standard calculations

### 3. Potential Parameter Uncertainty

#### Fitting Uncertainty
- **Source**: Fit quality to training data
- **Characterization**: RMSE, MAE from fitting paper
- **Typical magnitude**: 1-5% for fitted properties
- **Extraction**: Look in potential development paper

#### Transferability Uncertainty
- **Source**: Using potential outside fitted range
- **Types**:
  - Temperature extrapolation: ±2% per 100 K
  - Composition extrapolation: ±5-10% per 10% composition
  - Property extrapolation: ±10-20% for untested properties
- **Extraction**: Check potential fitting range in documentation

#### Validation Gap
- **Source**: Missing validation for target property
- **Characterization**: Comparison to available data
- **Typical magnitude**: ±10-30% if unvalidated
- **Extraction**: Check verification results (OpenKIM, potential paper)

## Extraction Methods

### From Published Papers

#### When Uncertainty Is Explicitly Reported
```
Direct use: "a = 5.470 ± 0.005 Å"
→ σ = 0.005 Å (0.09%)
```

#### When Only Error Bars Are Shown
```
Look at figures, estimate error bar size
→ If error bar spans 5.46-5.48 Å, σ ≈ 0.01 Å
→ Note: This is approximate, document assumption
```

#### When No Uncertainty Is Reported
```
Use typical values for measurement type:
- High-precision XRD: ±0.1%
- Standard XRD: ±0.5%
- Dilatometry: ±1%
- Elastic measurements: ±2-5%

Document: "Uncertainty estimated from typical measurement precision"
```

### From Computational Results

#### When Benchmarking Data Is Available
```
Compare to experiment:
- Calculated: 5.50 Å
- Experimental: 5.47 ± 0.01 Å
- Error: +0.03 Å (0.55%)
→ Use experimental uncertainty ± computational error
→ Total σ = √(0.01² + 0.03²) ≈ 0.032 Å (0.58%)
```

#### When No Benchmark Is Available
```
Use DFT functional error estimate:
- Standard GGA (PBE): ±5% systematic
- Add basis set uncertainty: ±2%
- Total σ ≈ √(5² + 2²) ≈ ±5.4%
→ For lattice constant 5.47 Å: σ ≈ 0.30 Å
```

### From Potential Fitting

#### When Fitting Statistics Are Reported
```
RMSE reported: 0.05 eV/atom for energy
→ Convert to relevant property:
→ For lattice constant, RMSE ~0.5-1%
→ Use reported RMSE as uncertainty
```

#### When Validation Data Is Available
```
OpenKIM verification shows:
- Lattice constant error: 0.5% vs experiment
- Elastic modulus error: 15% vs experiment
→ Use reported error as uncertainty for each property
```

## Uncertainty Propagation

### Mathematical Operations

#### Addition/Subtraction
```
y = a ± b
σ_y = √(σ_a² + σ_b²)

Example: ΔH = H_f(products) - H_f(reactants)
σ_ΔH = √(σ_prod² + σ_react²)
```

#### Multiplication/Division
```
y = a × b (or a / b)
(σ_y/y)² = (σ_a/a)² + (σ_b/b)²

Example: Volume V = a³
(σ_V/V) = 3 × (σ_a/a)
→ If a = 5.47 ± 0.01 Å (0.18%):
→ σ_V/V = 3 × 0.18% = 0.54%
```

#### Powers
```
y = aⁿ
(σ_y/y) = n × (σ_a/a)

Example: Density ρ = M/V (mass/volume)
→ ρ = mass × V⁻¹
→ (σ_ρ/ρ)² = (σ_m/m)² + (σ_V/V)²
```

### Functional Calculations

#### Linear Expansion Coefficient
```
α = (1/L) × (dL/dT)

Uncertainty components:
- σ_L from length measurement
- σ_T from temperature control
- σ_dL/dT from fit uncertainty

(σ_α/α)² = (σ_L/L)² + (σ_dL/dT / dL/dT)²
```

#### Elastic Moduli from Stress-Strain
```
E = dσ/dε

Uncertainty components:
- σ_σ from stress calculation/fit
- σ_ε from strain measurement

(σ_E/E)² = (σ_σ/σ)² + (σ_ε/ε)²
```

## Reporting Standards

### Minimum Reporting
```
Value: 5.470 Å
Uncertainty: ±0.010 Å (0.18%)
Source: Experimental XRD
Method: Direct measurement from paper
```

### Full Reporting (Preferred)
```
Value: 5.470 ± 0.010 Å (95% confidence)
Uncertainty breakdown:
  - Random error: ±0.003 Å (repeatability)
  - Systematic error: ±0.005 Å (calibration)
  - Sample purity: ±0.008 Å (UO₂.00 ± 0.01 stoichiometry)
  - Total: √(0.003² + 0.005² + 0.008²) ≈ ±0.010 Å
Measurement type: X-ray diffraction
Temperature: 300 K (±1 K)
Source: Smith et al., J. Nucl. Mater. (2020)
DOI: 10.1016/j.jnucmat.2020.01.001
```

### Computational Reporting
```
Value: 5.48 Å
Uncertainty: ±0.30 Å (5.5%)
Method: DFT-PBE with PAW pseudopotential
Uncertainty breakdown:
  - Functional error: ±0.27 Å (5% systematic)
  - Basis set: ±0.11 Å (2% from convergence test)
  - k-points: ±0.05 Å (1% estimated)
  - Total: √(5² + 2² + 1²) ≈ ±5.4%
Convergence: 500 eV cutoff, 8×8×8 k-points
Validation: Compared to experiment (error +0.18%)
Source: Materials Project mp-123456
```

## Estimation Guidelines

### When Uncertainty Is Not Reported

#### For Experimental Data
1. **Check methods section** for measurement precision
2. **Look at similar measurements** in same paper
3. **Use typical values**:
   - Lattice constants: ±0.5% (standard) to ±0.1% (high precision)
   - Elastic moduli: ±2-5%
   - Thermal expansion: ±5-10%
   - Phase transitions: ±5-15 K

#### For Computational Data
1. **Check if functional error** is discussed
2. **Look for convergence tests**
3. **Use default assumptions**:
   - DFT (standard): ±5%
   - DFT (hybrid): ±2%
   - Molecular dynamics: ±10-20%
   - Monte Carlo: ±10-20%

#### For Potentials
1. **Check potential development paper** for validation
2. **Look at OpenKIM verification results**
3. **Use typical values**:
   - Fitted properties: ±1-5%
   - Extrapolated properties: ±10-30%
   - Outside temperature range: ±2% per 100 K
   - Different composition: ±5-10% per 10% composition

## Combining Multiple Sources

### Weighted Average with Uncertainty
```
Two sources:
- Source 1: 5.470 ± 0.010 Å (weight: w₁)
- Source 2: 5.475 ± 0.015 Å (weight: w₂)

Weight: wᵢ = 1/σᵢ² (higher uncertainty = lower weight)
- w₁ = 1/0.010² = 10000
- w₂ = 1/0.015² = 4444

Weighted average:
a_avg = (w₁a₁ + w₂a₂) / (w₁ + w₂)
a_avg = (10000×5.470 + 4444×5.475) / 14444 ≈ 5.472 Å

Combined uncertainty:
σ_avg = √(1 / (w₁ + w₂))
σ_avg = √(1 / 14444) ≈ 0.008 Å
```

### Consistency Check
```
Check if sources agree within uncertainty:
- Difference: |5.470 - 5.475| = 0.005 Å
- Combined uncertainty: √(0.010² + 0.015²) ≈ 0.018 Å
- Since 0.005 < 0.018, sources are consistent

If difference > combined uncertainty:
→ Investigate systematic differences
→ Consider systematic error
→ Prefer higher-credibility source
```

## Uncertainty in Context

### For Reference Value Entry
```
Required uncertainty for autonomous use:
- Experimental: ±5% or better
- Computational: ±15% or better (includes DFT systematic error)
- Potential-based: ±20% or better (within fitted range)

If uncertainty larger:
→ Use with caution flag
→ May require human review
→ Document limitation explicitly
```

### For Conflict Resolution
```
When sources disagree:
1. Check if uncertainties overlap
2. If overlap exists → either acceptable, choose higher credibility
3. If no overlap → systematic difference, investigate:
   - Different measurement conditions?
   - Different samples (composition, purity)?
   - Different computational methods?
   - One source may be incorrect
4. Document rationale for final choice
```

### For Risk Assessment
```
When uncertainty impacts safety margins:
- Use worst-case within uncertainty range
- Apply safety factor: value × (1 + σ)
- For P0 systems, ensure uncertainty < 10%
- Larger uncertainty → escalate to human review
```

## Documentation Template

```
Property: [name]
Value: [numerical value with units]
Uncertainty: [± value or ± percent]
Confidence Interval: [95% CI if available]
Source: [citation]
Uncertainty Type: [experimental / computational / potential]
Uncertainty Breakdown:
  - Random error: [value]
  - Systematic error: [value]
  - Sample uncertainty: [value]
  - Method uncertainty: [value]
Total Uncertainty: [combined value]
Measurement Conditions: [temperature, pressure, etc.]
Notes: [any special considerations]
```

## Special Cases

### Temperature-Dependent Properties
```
Example: Thermal expansion coefficient α(T)

For each temperature point:
- α(T) = value ± σ_α(T)
- σ_α includes:
  - Measurement uncertainty (σ_meas)
  - Temperature control (σ_T)
  - Fit uncertainty (σ_fit)

If using interpolation:
- Add interpolation uncertainty (±10-20% of range)
- Total σ = √(σ_source² + σ_interp²)
```

### Composition-Dependent Properties
```
Example: U-Zr alloy lattice parameter vs. Zr content

For each composition:
- a(x) = value ± σ_a(x)
- σ_a includes:
  - Measurement uncertainty
  - Composition analysis uncertainty (σ_x)
  - Sample homogeneity uncertainty

When interpolating/extrapolating:
- Add composition uncertainty: ±0.5% per at% composition
- Verify Vegard's law applicability
- Note bowing parameters if significant deviation
```

### Phase Transition Data
```
Example: α-Zr → β-Zr transition temperature

Uncertainty includes:
- Measurement uncertainty: ±5 K (typical)
- Heating rate dependence: ±2-10 K (if applicable)
- Sample purity effect: ±1-5 K per impurity at%
- Hysteresis: ±5-20 K (first-order transitions)

Report as:
- T_transition = 1135 ± 15 K (heating rate 10 K/min)
- Note: Hysteresis observed, cooling 10 K lower
```
