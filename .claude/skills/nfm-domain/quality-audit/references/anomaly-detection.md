# Statistical Anomaly Detection Methods

## Overview

This document defines the statistical methods and detection criteria for identifying anomalies in nuclear materials reference data. Anomalies are classified into 6 types (A-F) with escalating severity.

## Detection Methods by Anomaly Type

### Type A: Missing Uncertainty (CRITICAL)

**Detection**: Direct schema validation — zero-tolerance.

```
pseudo:
FOR each reference_value IN P0 systems:
    IF reference_value.uncertainty IS NULL OR reference_value.uncertainty == "":
        FLAG as Type-A CRITICAL
```

**Expected Rate**: 0% (database rule enforces this)
**Action on Detection**: Immediate block — value cannot be used in safety analysis

### Type B: Low Credibility Source (HIGH)

**Detection**: Credibility tier check against literature-search scoring.

```
pseudo:
FOR each reference_value IN P0 systems:
    tier = CREDIBILITY_SCORE(reference_value.source)
    IF tier < 2:
        FLAG as Type-B HIGH
```

**Credibility Tiers** (from literature-search credibility-scoring):
- Tier 1: Peer-reviewed journal, validated experiment (score ≥8)
- Tier 2: Established database (NIST, Materials Project), validated model (score 5-7)
- Tier 3: Conference paper, unreviewed preprint (score 3-4)
- Tier 4: Personal communication, unpublished (score 1-2)

**Expected Rate**: <5%
**Action on Detection**: Find higher-tier source or escalate for human review

### Type C: Outdated Verification (MEDIUM)

**Detection**: Date comparison.

```
pseudo:
current_date = TODAY()
cutoff_date = current_date - 3_YEARS
FOR each reference_value IN P0 systems:
    IF reference_value.last_verified < cutoff_date:
        FLAG as Type-C MEDIUM
```

**Expected Rate**: <10%
**Action on Detection**: Schedule re-verification; value usable with caution if <5 years old

### Type D: Value Out of Range (MEDIUM to HIGH)

**Detection**: Compare against known physical property ranges.

**Expected Ranges for P0 Materials**:

| Property | U (α) | UO2 | Zr (α) | Fe (α) | U-Zr |
|----------|-------|-----|--------|--------|------|
| Density (g/cm³) | 18.7-19.1 | 10.6-11.0 | 6.4-6.6 | 7.8-7.9 | 13-16 |
| Melting Point (K) | 1400-1410 | 3100-3150 | 2120-2130 | 1800-1820 | varies |
| Thermal Cond. (W/m·K) | 25-30 | 3-8 | 21-23 | 75-85 | 15-30 |
| Lattice Param a (Å) | 2.8-2.9 | 5.4-5.5 | 3.2-3.3 | 2.8-2.9 | 3.4-3.6 |

```
pseudo:
FOR each reference_value IN P0 systems:
    expected_range = MATERIAL_PROPERTY_RANGES[value.material][value.property]
    IF value.mean ± value.uncertainty OUTSIDE expected_range:
        FLAG as Type-D (severity depends on deviation)
```

**Severity Assignment**:
- Within 2σ of expected range: MEDIUM
- 2σ-5σ outside: HIGH
- \>5σ outside: CRITICAL (likely measurement error)

### Type E: Inconsistent Uncertainty (LOW to MEDIUM)

**Detection**: Statistical analysis of uncertainty distribution.

**Z-score Method**:
```
pseudo:
FOR each material + property group:
    uncertainties = [v.uncertainty for v in group]
    μ = MEAN(uncertainties)
    σ = STD(uncertainties)
    FOR each value in group:
        z = |value.uncertainty - μ| / σ
        IF z > 3:
            FLAG as Type-E (too small if z<0, too large if z>0)
```

**Hard Bounds** (override Z-score):
- Uncertainty <0.5%: Too small (MEDIUM)
- Uncertainty >30%: Too large (MEDIUM)
- Zero uncertainty: CRITICAL (treated as Type A)

### Type F: Confidence Drop (LOW)

**Detection**: Trend analysis across quarterly audits.

```
pseudo:
FOR each P0 system:
    current_score = CALCULATE_COMPLIANCE(system, THIS_QUARTER)
    previous_score = CALCULATE_COMPLIANCE(system, PREVIOUS_QUARTER)
    trend = [score for score in LAST_4_QUARTERS]
    IF current_score < previous_score AND trend IS DECLINING:
        FLAG as Type-F LOW
    IF current_score < previous_score - 10%:
        ESCALATE to MEDIUM
```

## Statistical Methods Reference

### Outlier Detection (General)

**IQR Method** (for non-parametric data):
```
Q1 = PERCENTILE(data, 25)
Q3 = PERCENTILE(data, 75)
IQR = Q3 - Q1
lower_fence = Q1 - 1.5 * IQR
upper_fence = Q3 + 1.5 * IQR
outliers = [x for x in data if x < lower_fence OR x > upper_fence]
```

**Modified Z-Score** (robust to outliers):
```
MAD = MEDIAN(|x_i - MEDIAN(data)|)
modified_z = 0.6745 * (x_i - MEDIAN(data)) / MAD
outliers = [x_i for x_i where |modified_z| > 3.5]
```

### Trend Analysis (Type F)

**Linear Regression**:
```
For last N quarterly compliance scores:
    slope = COVARIANNCE(time, scores) / VARIANNCE(time)
    IF slope < -2.0: DECLINING
    IF slope < -5.0: RAPIDLY DECLINING → escalate
```

**Mann-Kendall Test** (non-parametric trend):
- τ < 0 and p < 0.05: Significant downward trend
- Use when N ≥ 4 quarterly points

## Automated Detection Workflow

```
INPUT: P0 reference values from database
OUTPUT: Anomaly list with type and severity

STEP 1: Schema validation (Type A)
  → Immediate: block missing-uncertainty values

STEP 2: Credibility check (Type B)
  → Run credibility scoring from literature-search

STEP 3: Recency check (Type C)
  → Date comparison against 3-year cutoff

STEP 4: Range validation (Type D)
  → Compare against expected physical ranges

STEP 5: Uncertainty analysis (Type E)
  → Z-score and hard-bound checks

STEP 6: Trend analysis (Type F)
  → Compare against previous quarter scores

OUTPUT: Prioritized anomaly list:
  1. CRITICAL (Type A, severe Type D)
  2. HIGH (Type B, moderate Type D)
  3. MEDIUM (Type C, moderate Type E, severe Type F)
  4. LOW (Type E, Type F)
```

## Integration with Quality Audit

All anomaly types feed into the main audit workflow:
- CRITICAL → Block audit pass, require immediate remediation
- HIGH → Flag in executive summary, require action this quarter
- MEDIUM → Include in detailed findings
- LOW → Track for trend analysis

## Cross-References

- See p0-checklist.md for the full validation checklist
- See report-generation.md for anomaly reporting format
- See literature-search for credibility scoring implementation
