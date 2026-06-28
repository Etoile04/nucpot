# P0 Data Quality Checklist

## Checklist Purpose

This checklist defines mandatory quality requirements for P0 (Priority Zero) nuclear material reference values in the verification database. Each reference value must pass all items for autonomous use in safety-critical applications.

## P0 Material Systems

**Primary P0 Systems**:
1. **Uranium (U)** - Fuel material
2. **Uranium Dioxide (UO₂)** - Primary fuel oxide
3. **Zirconium (Zr)** - Cladding material
4. **Iron (Fe)** - Structural material
5. **U-Zr Alloys** - Metallic fuel systems

**System Definition**: Each unique material + phase combination is a separate P0 system.

## Mandatory Checklist Items

### Item 1: Uncertainty Documentation

**Requirement**: Reference value must include quantitative uncertainty

**Validation**:
- [ ] Uncertainty field populated (not null, not empty string)
- [ ] Uncertainty includes units or percentage
- [ ] Uncertainty value not zero (unless justified)
- [ ] Uncertainty format follows convention: `value ± uncertainty` or `value (uncertainty%)`

**Acceptable Formats**:
```
✓ 5.470 ± 0.010 Å
✓ 5.470 Å (±0.18%)
✓ 298 ± 5 K
✓ 298 K (±1.7%)

✗ 5.470 Å (no uncertainty)
✗ 5.470 Å (±0)
✗ 5.470 Å (uncertain)
```

**Justification for Zero Uncertainty**:
- Standard reference values (fundamental constants)
- Must be documented with reason (e.g., "IAEA standard value")

**Verification Method**:
```sql
SELECT COUNT(*) as total,
       SUM(CASE WHEN uncertainty IS NULL THEN 1 ELSE 0 END) as missing
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0';
```

---

### Item 2: Source Credibility

**Requirement**: Source must have credibility score ≥70 (Tier 2 or higher)

**Credibility Tiers**:
- **Tier 1** (90-100): Gold standard, peer-reviewed, high-precision experimental
- **Tier 2** (70-89): Reliable source, peer-reviewed or standard database
- **Tier 3** (50-69): Moderate credibility, requires caution
- **Tier 4** (0-49): Low credibility, not acceptable for autonomous use

**Acceptable Sources**:
- ✓ NIST IPR reviewed potentials (Tier 1-2)
- ✓ OpenKIM verified models (Tier 1-2)
- ✓ Materials Project DFT with validation (Tier 2)
- ✓ Peer-reviewed journals (Tier 2-3)
- ✗ Preprints without peer review (Tier 3)
- ✗ Unvalidated computational results (Tier 4)

**Verification Method**:
```sql
SELECT source_id, source_type, credibility_score, credibility_tier
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0'
  AND credibility_score < 70;
```

**Escalation Trigger**:
- If >5% of P0 values have low-credibility sources
- If any critical value uses Tier 3+ source without human review

---

### Item 3: Verification Recency

**Requirement**: Verification must be within 3 years of current date

**Recency Tiers**:
- **Current** (0-1 year): Verified within last year
- **Recent** (1-3 years): Verified 1-3 years ago
- **Outdated** (>3 years): Requires re-verification

**Acceptable Timeline**:
- ✓ Verification date > 2023 (assuming current year 2026)
- ✗ Verification date < 2023 (older than 3 years)

**Verification Activities That Count**:
- Literature search confirming value still valid
- Experimental re-measurement
- Computational validation with newer methods
- Cross-check with updated databases
- Expert review confirming value currency

**Verification Method**:
```sql
SELECT value_id, verification_date,
       DATEDIFF(CURRENT_DATE, verification_date) as years_since
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0'
  AND verification_date < DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR);
```

**Re-Verification Priority**:
1. **Critical**: Values used in safety calculations
2. **High**: Core material properties (lattice, elastic)
3. **Medium**: Derived properties (thermal expansion)
4. **Low**: Supplementary data

---

### Item 4: Conflict Resolution

**Requirement**: No unresolved conflicts between values for same property/condition

**Conflict Types**:
- **Direct Conflict**: Same property/condition has different values
- **Scientific Conflict**: Values disagree beyond combined uncertainty
- **Source Conflict**: Tier 1 sources disagree

**Resolution Requirements**:
- [ ] Conflict identified (same property/condition, different values)
- [ ] Primary value selected
- [ ] Rationale documented
- [ ] Alternative values noted
- [ ] Decision-maker (human or algorithm) recorded
- [ ] Resolution date recorded

**Acceptable Resolution**:
```
Conflict: UO₂ lattice constant at 300 K
Source A: 5.470 ± 0.005 Å (Smith et al. 2022)
Source B: 5.475 ± 0.010 Å (Jones et al. 2020)

Resolution:
- Primary: 5.470 ± 0.005 Å (Source A)
- Rationale: Higher precision, more recent, Tier 1 source
- Alternative: 5.475 ± 0.010 Å (Source B)
- Decision: Skills Architect (automated) on 2026-06-15
```

**Unresolved Conflicts**:
```
✗ Value 1 selected, Value 2 not documented
✗ No rationale provided
✗ Decision-maker not recorded
✗ Both values marked as "primary"
```

**Verification Method**:
```sql
-- Find conflicts
SELECT property, temperature, value, uncertainty,
       COUNT(*) as conflict_count
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0'
  AND is_primary = true
GROUP BY property, temperature
HAVING conflict_count > 1;
```

---

### Item 5: Source Traceability

**Requirement**: Source must be traceable to persistent identifier

**Required Identifiers**:
- [ ] DOI (Digital Object Identifier) - preferred for literature
- [ ] Database ID - for Materials Project, NIST IPR, OpenKIM
- [ ] Report ID - for technical reports
- [ ] Persistent URL - for web sources

**Minimum Documentation**:
```
✓ DOI: 10.1016/j.jnucmat.2020.01.001
✓ Database ID: mp-123456 (Materials Project)
✓ NIST IPR ID: U_u3.eam
✓ Citation: "Smith et al., J. Nucl. Mater. 500 (2020) 123-456"
✗ URL: https://website.com/data (may break)
✗ Citation: "Various sources" (not traceable)
```

**Verification Method**:
```sql
SELECT value_id, source_type, source_id
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0'
  AND (source_id IS NULL OR source_id = '');
```

---

## Property-Specific Requirements

### Structural Properties

#### Lattice Constant
- **Required**: Uncertainty ≤0.5% (high precision) or ≤2% (standard)
- **Acceptable Format**: Å with ± uncertainty
- **Example**: `5.470 ± 0.010 Å`

#### Elastic Moduli
- **Required**: Uncertainty ≤5%
- **Acceptable Format**: GPa with ± uncertainty or percentage
- **Example**: `200 ± 10 GPa` or `200 GPa (±5%)`

#### Density
- **Required**: Uncertainty ≤2%
- **Acceptable Format**: g/cm³ with ± uncertainty or percentage
- **Example**: `10.96 ± 0.22 g/cm³`

### Thermal Properties

#### Thermal Expansion Coefficient
- **Required**: Uncertainty ≤10%
- **Required**: Temperature range specified
- **Acceptable Format**: K⁻¹ with ± uncertainty
- **Example**: `10.5 ± 1.0×10⁻⁶ K⁻¹` for 300-800 K range

#### Heat Capacity
- **Required**: Uncertainty ≤5%
- **Required**: Temperature specified
- **Acceptable Format**: J/mol·K with ± uncertainty
- `Example`: `30.5 ± 1.5 J/mol·K` at 300 K

### Thermodynamic Properties

#### Formation Energy
- **Required**: Uncertainty ≤10% (DFT) or ≤2% (high-precision experiment)
- **Required**: Reference state specified
- **Acceptable Format**: eV/atom or kJ/mol with ± uncertainty
- **Example**: `-3.5 ± 0.4 eV/atom` (DFT) or `-350 ± 7 kJ/mol`

#### Phase Transition Temperature
- **Required**: Uncertainty ≤15 K (first-order) or ≤5 K (second-order)
- **Required**: Heating/cooling rate noted
- **Acceptable Format**: K with ± uncertainty
- **Example**: `1135 ± 15 K` (α-Zr → β-Zr transition)

---

## Database Query Examples

### Complete P0 Compliance Check
```sql
-- Count passing P0 items by system
SELECT
    material_system,
    property_type,
    COUNT(*) as total_items,
    SUM(CASE 
        WHEN uncertainty IS NOT NULL 
            AND uncertainty != '' 
            AND credibility_score >= 70 
            AND verification_date >= DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR)
            AND (conflict_status IS NULL OR conflict_status = 'resolved')
        THEN 1 ELSE 0 
    END) as passing_items,
    SUM(CASE 
        WHEN uncertainty IS NULL 
            OR uncertainty = '' 
            OR credibility_score < 70 
            OR verification_date < DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR)
        THEN 1 ELSE 0 
    END) as failing_items,
    (SUM(CASE WHEN uncertainty IS NOT NULL AND credibility_score >= 70 
            AND verification_date >= DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR)
        THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)) * 100 as compliance_percentage
FROM reference_values
WHERE material_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
  AND priority = 'P0'
GROUP BY material_system, property_type
ORDER BY compliance_percentage DESC;
```

### Identify Anomalies
```sql
-- Missing uncertainty
SELECT value_id, material_system, property, value, temperature
FROM reference_values
WHERE priority = 'P0'
  AND (uncertainty IS NULL OR uncertainty = '');

-- Low credibility
SELECT value_id, material_system, property, source_type, credibility_score
FROM reference_values
WHERE priority = 'P0'
  AND credibility_score < 70;

-- Outdated verification
SELECT value_id, material_system, property, verification_date,
       DATEDIFF(CURRENT_DATE, verification_date) as years_old
FROM reference_values
WHERE priority = 'P0'
  AND verification_date < DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR);

-- Unresolved conflicts
SELECT value_id, material_system, property, value, conflict_status
FROM reference_values
WHERE priority = 'P0'
  AND conflict_status = 'unresolved';
```

### System-by-System Compliance
```sql
-- Comprehensive system compliance
WITH system_stats AS (
    SELECT
        material_system,
        COUNT(*) as total_values,
        SUM(CASE WHEN uncertainty IS NOT NULL AND uncertainty != '' THEN 1 ELSE 0 END) as with_uncertainty,
        SUM(CASE WHEN credibility_score >= 70 THEN 1 ELSE 0 END) as credible_sources,
        SUM(CASE WHEN verification_date >= DATE_SUB(CURRENT_DATE, INTERVAL 3 YEAR) THEN 1 ELSE 0 END) as recent_verification
    FROM reference_values
    WHERE priority = 'P0'
    GROUP BY material_system
)
SELECT
    material_system,
    total_values,
    with_uncertainty * 100.0 / total_values as uncertainty_coverage,
    credible_sources * 100.0 / total_values as credibility_coverage,
    recent_verification * 100.0 / total_values as verification_coverage,
    (with_uncertainty + credible_sources + recent_verification) * 100.0 / (total_values * 3) as overall_compliance
FROM system_stats
ORDER BY overall_compliance DESC;
```

---

## Audit Execution Guide

### Quarterly Audit Procedure

#### Phase 1: Preparation (Week 1)
1. **Define audit scope**
   - List all P0 material systems
   - Define audit period (previous quarter)
   - Identify any new systems added

2. **Extract reference values**
   - Query database for all P0 reference values
   - Export to audit spreadsheet
   - Prepare for manual review if needed

3. **Prepare checklist tools**
   - Create SQL queries for automated checks
   - Prepare statistical analysis scripts
   - Set up anomaly detection

#### Phase 2: Execution (Week 1-2)
1. **Run automated checklist**
   - Execute all 5 mandatory items
   - Generate compliance statistics
   - Flag failing items

2. **Detect anomalies**
   - Statistical outlier detection
   - Trend analysis (degradation over time)
   - Cross-system consistency checks

3. **Manual review (if needed)**
   - Review flagged items
   - Investigate anomalies
   - Contextual validation

#### Phase 3: Analysis (Week 2)
1. **Analyze results**
   - Calculate compliance percentages
   - Identify systematic issues
   - Compare to previous audits

2. **Generate report**
   - Use report template from [references/report-generation.md](references/report-generation.md)
   - Include all sections
   - Highlight critical issues

3. **Recommend actions**
   - Prioritize remediation
   - Estimate effort required
   - Assign owners and timelines

#### Phase 4: Resolution (Week 3-4)
1. **Implement fixes**
   - Add missing uncertainties via literature-search
   - Update low-credibility sources
   - Re-verify outdated values
   - Resolve conflicts

2. **Re-audit**
   - Run checklist on fixed items
   - Verify compliance improved
   - Document final compliance

3. **Final report**
   - Update audit report with final status
   - Mark audit complete
   - Provide recommendations for next quarter

---

## Compliance Scoring

### System-Level Compliance Calculation

For each P0 material system:

```
Compliance % = (Passing Items / Total Items) × 100

Where Passing Items = All 5 checklist items satisfied:
1. Uncertainty documented
2. Credibility ≥ 70 (Tier 2+)
3. Verification within 3 years
4. No unresolved conflicts
5. Source traceable
```

### Overall Database Compliance

```
Overall Compliance = Average(System Compliance % for all 5 P0 systems)

Acceptable: ≥98% (allowing 2% issues)
Marginal: 95-98% (requires action)
Critical: <95% (immediate remediation required)
```

---

## Failure Mode Analysis

### Common Failure Patterns

#### Pattern 1: Missing Uncertainty (Typical)
- **Frequency**: Should be 0% (database constraint)
- **Impact**: Cannot use in safety calculations
- **Fix**: Use literature-search skill to find uncertainty

#### Pattern 2: Outdated Verification (Common)
- **Frequency**: 10-30% typical (literature evolves)
- **Impact**: May not reflect current state
- **Fix**: Re-verify using literature-search skill

#### Pattern 3: Low Credibility Sources (Occasional)
- **Frequency**: 5-10% typical (new literature not yet vetted)
- **Impact**: Questionable reliability
- **Fix**: Use literature-search to find Tier 1-2 sources

#### Pattern 4: Value Conflicts (Rare)
- **Frequency**: 2-5% typical
- **Impact**: Ambiguity in correct value
- **Fix**: Apply conflict resolution procedure

#### Pattern 5: Untraceable Sources (Occasional)
- **Frequency**: 5-10% typical (web resources break)
- **Impact**: Cannot validate source
- **Fix**: Use literature-search to find persistent identifiers

---

## Audit Templates

### Template 1: Individual Item Check
```
System: UO₂
Item: Uncertainty Documentation
Property: Lattice constant at 300 K
Value: 5.470 Å

Check:
□ Uncertainty field not NULL?
□ Uncertainty has units or percentage?
□ Uncertainty not zero (unless justified)?
□ Uncertainty < 0.5% (high precision requirement)?

Result: PASS / FAIL
Notes: [Any concerns or observations]
```

### Template 2: System Compliance Summary
```
P0 System: UO₂
Audit Period: Q1 2026
Audit Date: 2026-04-15

Compliance Summary:
- Total Items: 125
- Passing Items: 120
- Compliance: 96%
- Status: PASS (≥95%)

Item Breakdown:
- Uncertainty Documentation: 125/125 (100%)
- Source Credibility: 120/125 (96%)
- Verification Recency: 122/125 (98%)
- Conflict Resolution: 119/125 (95%)
- Source Traceability: 123/125 (98%)

Critical Issues:
- Item 78: Low credibility source (Tier 3)
- Item 89: Verification 2.5 years old (marginal)
- Item 105: Minor conflict (need resolution)

Confidence: 96%
Recommendation: Continue monitoring, update 3 items
```

---

## Human Review Triggers

### Automatic Escalation (Confidence <70%)

**Trigger Conditions**:
- Overall compliance <95%
- Critical anomaly (Type A or B) found
- System compliance <90% for any P0 system
- More than 10% items failing same checklist item

### Human Review Required

**For These Situations**:
1. **Conflicts requiring domain knowledge**: Values disagree beyond uncertainty, requires expert judgment
2. **New material systems**: First-time P0 system addition
3. **Major database updates**: Bulk import or migration
4. **Regulatory changes**: New safety requirements
5. **Performance issues**: Audit takes too long or finds too many issues

**Human Review Process**:
1. Skills Architect generates audit report with findings
2. Quality Auditor reviews flagged items
3. CTO makes final decision on compliance
4. Documentation of decisions for future reference

---

## Data Quality Metrics

### Track Quarterly

1. **Overall Compliance Percentage**
   - Target: ≥98%
   - Monitor: Trend over time

2. **Per-System Compliance**
   - Target: ≥95% for each system
   - Monitor: Identify degrading systems

3. **Anomaly Counts**
   - Track each anomaly type count
   - Target: Zero Type A, <5 Type B/C

4. **Fix Lag Time**
   - From detection to resolution
   - Target: <30 days for typical issues
   - Monitor: Backlog of unresolved items

5. **Source Quality Trends**
   - Percentage using Tier 1 sources
   - Percentage with recent verification
   - Target: Increase over time

### Annual Review

At year-end, prepare comprehensive report:
- Compliance trend over 4 quarters
- System reliability metrics
- Source quality evolution
- Lessons learned and improvements
- Recommendations for next year
