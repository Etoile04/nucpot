---
name: quality-audit
description: Quarterly P0 data quality audit for nuclear materials verification database: systematic checks, anomaly detection, uncertainty coverage validation, report generation. Use when auditing reference values, checking compliance, or generating quality reports.
---

<objective>
Provides comprehensive quality auditing for P0 safety-critical nuclear materials data, including systematic checklist validation, automated anomaly detection, and structured report generation.
</objective>

<audit_framework>
P0 data quality criteria and audit procedures, see:
- [references/p0-checklist.md](references/p0-checklist.md) - Complete P0 data quality checklist
- [references/anomaly-detection.md](references/anomaly-detection.md) - Statistical anomaly detection methods
- [references/report-generation.md](references/report-generation.md) - Audit report templates and formats
</audit_framework>

<p0_definition>
**P0 (Priority Zero) Systems**: Safety-critical nuclear material systems requiring 100% quality assurance

**P0 Material Systems**:
1. **Uranium (U)** - Fuel material
2. **Uranium Dioxide (UO₂)** - Primary fuel form
3. **Zirconium (Zr)** - Cladding material
4. **U-Zr Alloys** - Metallic fuel systems

**P0 Quality Requirements**:
- 100% reference values must have uncertainty documentation
- 100% reference values must have source credibility ≥ Tier 2
- 100% reference values must have verification within last 3 years
- 0% conflicts (or all conflicts resolved with documentation)
- 100% traceability to source (DOI, database ID)

**Consequences of Non-Compliance**:
- Missing uncertainty: Reference value invalid for safety analysis
- Low credibility source: Requires human review before use
- Outdated verification: Re-verification required before use
- Unresolved conflict: Cannot be used in safety calculations
</p0_definition>

<workflow>
When performing quarterly P0 quality audit:

1. **Scope the audit**
   - Identify all P0 systems in database
   - Define audit period (previous quarter)
   - Check NFM-87 decision doc for system list

2. **Run P0 checklist**
   - Use [references/p0-checklist.md](references/p0-checklist.md)
   - For each P0 system, verify:
     * Uncertainty coverage (100% required)
     * Source credibility (Tier 2+ required)
     * Verification recency (within 3 years)
     * Conflict resolution status
   - Track compliance percentage

3. **Detect anomalies**
   - Use [references/anomaly-detection.md](references/anomaly-detection.md)
   - Statistical analysis for outliers:
     * Values outside expected ranges
     * Inconsistent uncertainties (too high/low)
     * Credibility drops over time
     * Temperature extrapolations without justification
   - Flag items requiring human review

4. **Generate audit report**
   - Use [references/report-generation.md](references/report-generation.md)
   - Include:
     * Executive summary (pass/fail rates)
     * System-by-system breakdown
     * Anomaly list with severity
     * Recommendations for remediation
     * Confidence score for overall database quality

5. **Document findings**
   - Update issue with audit results
   - Flag items requiring immediate action
   - Create child issues for remediation if needed
   - Mark audit complete with disposition

6. **Human review trigger** (when needed)
   - If confidence <70% or critical anomalies found
   - Escalate to Quality Auditor or CTO
   - Provide structured summary of concerns
</workflow>

<checklist_validation>
**P0 Checklist Item Scoring**:

Each P0 reference value evaluated on:

**MUST PASS (Zero Tolerance)**:
1. Uncertainty documented (YES/NO)
2. Source credibility ≥ Tier 2 (YES/NO)
3. Verification within 3 years (YES/NO)
4. No unresolved conflicts (YES/NO)
5. Traceable source (YES/NO)

**Compliance Calculation**:
- System pass rate = (passing items / total items) × 100%
- Overall pass rate = average of all P0 systems

**Pass Criteria**:
- Individual system: ≥95% (allowing 5% minor issues)
- Overall database: ≥98% (allowing 2% issues)
- Any item failing "MUST PASS" → Critical issue requiring escalation
</checklist_validation>

<anomaly_types>
**Type A: Missing Uncertainty** (Severity: CRITICAL)
- Definition: Reference value without ± uncertainty
- Impact: Cannot be used in safety analysis
- Frequency: Should be 0% (database rule)
- Detection: Direct check of uncertainty field
- Resolution: Add uncertainty via literature-search

**Type B: Low Credibility Source** (Severity: HIGH)
- Definition: Source credibility < Tier 2
- Impact: May not be reliable for safety calculations
- Frequency: Should be <5%
- Detection: Credibility scoring check
- Resolution: Find higher-credibility source or human review

**Type C: Outdated Verification** (Severity: MEDIUM)
- Definition: Last verification > 3 years ago
- Impact: May not reflect current material state
- Frequency: Should be <10%
- Detection: Check verification date vs. current date
- Resolution: Re-verify with current literature

**Type D: Value Out of Range** (Severity: MEDIUM to HIGH)
- Definition: Value ± uncertainty outside expected physical range
- Impact: May indicate measurement error or wrong phase
- Frequency: <5%
- Detection: Compare to material property databases
- Resolution: Investigate and validate or correct value

**Type E: Inconsistent Uncertainty** (Severity: LOW to MEDIUM)
- Definition: Uncertainty too small (<1%) or too large (>20%)
- Impact: Questionable reliability
- Frequency: <10%
- Detection: Statistical analysis of uncertainty distribution
- Resolution: Verify uncertainty with source

**Type F: Confidence Drop** (Severity: LOW)
- Definition: System quality degrading over time
- Impact: Trend indicates systematic issue
- Frequency: Check quarterly trends
- Detection: Compare current audit to previous audits
- Resolution: Investigate root cause of degradation
</anomaly_types>

<output_format>
Structure audit reports as:

**Executive Summary**:
- Overall compliance: [X%]
- Critical issues: [count]
- Systems audited: [list with pass/fail]
- Overall confidence: [0-100%]

**System-by-System Results**:
- [System 1]: [X% compliance, key issues]
- [System 2]: [X% compliance, key issues]
- [System 3]: [X% compliance, key issues]
- [System 4]: [X% compliance, key issues]
- [System 5]: [X% compliance, key issues]

**Anomaly Summary**:
- Type A (Missing Uncertainty): [count]
- Type B (Low Credibility): [count]
- Type C (Outdated Verification): [count]
- Type D (Out of Range): [count]
- Type E (Inconsistent Uncertainty): [count]
- Type F (Confidence Drop): [count]

**Detailed Anomalies**:
- [Item ID]: [anomaly description, severity]
- [Item ID]: [anomaly description, severity]
- [... more as needed]

**Recommendations**:
- [Priority 1 - Critical]: [immediate actions needed]
- [Priority 2 - High]: [actions this quarter]
- [Priority 3 - Medium]: [actions next quarter]

**Disposition**:
- Overall audit status: [PASS / FAIL / USE WITH CAUTION]
- Confidence score: [0-100%]
- Required follow-up: [yes/no, with owner]
</output_format>

<validation>
A complete quality audit includes:
- All 5 P0 systems audited (U, UO₂, Zr, Fe, U-Zr)
- Each reference value checked against P0 checklist
- Statistical anomaly detection performed
- Comprehensive report generated with all sections
- Critical issues flagged for immediate action
- Confidence score ≥70% for autonomous sign-off
- Human escalation if confidence <70% or critical anomalies found

Never:
- Skip P0 systems in audit (all 5 must be checked)
- Ignore anomalies (all must be documented)
- Use incomplete data (flag for remediation first)
- Sign off audit without addressing critical issues
- Report pass rate without detailed system breakdown
</validation>

<integration_notes>
This skill works with other nuclear domain skills:
- **literature-search** - Validates sources and finds updated values
- **nuclear-materials-knowledge** - Provides expected property ranges
- **lammps-debugger** - Identifies simulation quality issues

Common workflow: quality-audit finds issues → literature-search finds solutions → nuclear-materials-knowledge validates → quality-audit verifies fix
</integration_notes>

<audit_frequency>
**Quarterly Audit Schedule**:
- **Q1 (Jan-Mar)**: Audit all P0 systems
- **Q2 (Apr-Jun)**: Audit all P0 systems
- **Q3 (Jul-Sep)**: Audit all P0 systems
- **Q4 (Oct-Dec)**: Audit all P0 systems

**Trigger-Based Audit** (in addition to quarterly):
- After major database updates
- After NFM-83 system implementation changes
- After critical safety incident
- When confidence score drops below 80%

**Audit Timeline**:
- Week 1: Execute checklist and anomaly detection
- Week 2: Investigate anomalies, generate report
- Week 3: Human review (if needed) and remediation planning
- Week 4: Implement fixes, verify compliance
</audit_frequency>

<report_templates>
Audit reports use standardized format for consistency:

**Report Filename**: `audit_QY_YYYY.pdf`
**Distribution**: CTO, Quality Auditor, CEO (as requested)
**Retention**: Keep all historical audit reports for trend analysis

**Template Location**: [references/report-generation.md](references/report-generation.md)
**Generation**: Semi-automated from audit database + anomaly analysis
</report_templates>
