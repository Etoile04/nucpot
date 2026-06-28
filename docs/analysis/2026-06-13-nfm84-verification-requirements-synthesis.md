# NFM-84 Verification Requirements Synthesis

**Date**: 2026-06-13  
**Source**: Domain Expert Consultation (Lili - NFMD Coach)  
**Status**: Complete ✅  
**Disposition**: Ready for implementation planning

---

## Executive Summary

Lili's consultation provides a complete blueprint for the NucPot verification gap system. Key finding: **The current verification framework is 65% functional but has critical data quality gaps**—most notably, **no uncertainty estimates in reference values**, which is the single biggest data quality issue.

### Current State
- **5 core properties** automated: lattice constant, cohesive energy, elastic constants, bulk modulus, vacancy formation energy
- **A-F grading** implemented (A≤2%, B≤5%, C≤10%, D≤20%, F>20%)
- **Coverage**: 42/65 potentials verifiable, only 16/19 recent verifications succeeded
- **Grade distribution**: A×1, B×2, C×9, D×3, F×1 (concentrated at C level)

### Critical Gaps Identified
1. **19 potentials** lack files for verification (non-EAM types: Buckingham, Tersoff, AIREBO)
2. **Many materials** only have lattice constant reference values—missing elastic constants and vacancy formation energies
3. **No uncertainty estimates** in reference values—biggest data quality gap
4. **Important systems** (U-Pu-Zr, UN, UC, SiC) have **no reference values at all**

---

## 1. Verification Gap Definition Framework

Lili defines "verification gap" as a **three-layer combination**:

```
Verification Gap = Σ(
  Unverified Potential,    — Potential exists but not verified
  Uncovered Property,      — Potential verified but missing properties
  Missing Reference        — Property calculable but no reference value for grading
)
```

### Gap Classification (P0-P4)

| Priority | Name | Criteria | Impact |
|----------|------|----------|--------|
| **P0 Critical** | Safety-Critical Gap | Core systems (U, UO₂, Zr, U-Zr, Fe) missing core properties (lattice, elastic, vacancy) | Affects reactor safety analysis reliability |
| **P1 High** | Performance Gap | Core systems missing secondary properties (surface energy, diffusion, mixing enthalpy) | Affects radiation damage, fuel performance modeling |
| **P2 Medium** | System Coverage Gap | Important but non-core systems (U-Pu-Zr, UN, UC, SiC) missing verification | Affects advanced fuel and accident-tolerant fuel research |
| **P3 Low** | Property Extension Gap | All systems missing extended properties (thermal conductivity, melting point, stacking fault) | Affects comprehensive material performance assessment |
| **P4 Info** | Data Quality Gap | Incomplete reference source tags, missing uncertainty estimates, missing literature DOIs | Affects user confidence in verification results |

---

## 2. Priority Areas for Verification Data Collection

### First Priority (P0): Safety-Critical Systems

**Nuclear material safety analysis relies on these potential × core property combinations:**

- **U (BCC)** — lattice constant, elastic constants, vacancy formation energy
- **UO₂ (fluorite)** — lattice constant, elastic constants, vacancy formation energy  
- **Zr (HCP)** — lattice constant, elastic constants, vacancy formation energy
- **U-Zr (BCC)** — lattice constant, elastic constants
- **Fe (BCC)** — lattice constant, elastic constants, vacancy formation energy (steel structure baseline)

**Consequences of missing verification:**
- Researchers cannot judge if potential is suitable for specific applications
- Safety-related simulations (fuel cladding radiation damage) using unverified potentials have questionable reliability
- Cannot fairly compare and select between different potentials

---

## 3. Stakeholder Requirements

### Quality Thresholds by Use Case

| Stakeholder | Minimum Grade | Core Property Coverage | Uncertainty Tolerance |
|-------------|---------------|----------------------|----------------------|
| Nuclear Material Researchers | C (≤10% deviation) for preliminary screening | ≥ 3/5 | ±10% acceptable |
| Reactor Engineers | B (≤5% deviation) recommended for engineering calculations | ≥ 4/5 | ±5% target |
| Safety Analysts | A (≤2% deviation) required for critical properties | 5/5 full coverage | ±2% target, with uncertainty required |
| Potential Developers | Detailed deviation report by property | — | Need reference source and literature DOI |

### Coverage Requirements

**Must-Verify Systems (safety-related):**
- U (α-U), UO₂, Zr (α-Zr), Fe (α-Fe), Zr-Nb — these 5 are reactor core materials

**Should-Verify Systems (performance-related):**
- U-Zr, U-Mo, U-Pu-Zr, Mo, Nb — fuel and cladding alloys

**Minimum Completeness Standards:**
- Each potential covers at least 3 core properties (lattice constant, elastic constants, vacancy formation energy)
- Safety-related potentials' 3 core properties achieve at least C grade
- Acceptable gap ratio: safety-related ≤10% unverified; others ≤30% unverified

---

## 4. Reporting Format Specification

### Required Content

```markdown
1. Executive Summary
   - Total potentials, verified count, coverage percentage
   - Grade distribution (A/B/C/D/F/unverified)
   - Trend since last report

2. System-Level Coverage Matrix
   - Rows: elements/systems
   - Columns: properties (lattice_constant, C11, C12, C44, E_vac, ...)
   - Cells: grade (A-F) or "—" (unverified) or "?" (no reference)

3. Critical Gap List
   - Sorted by severity (P0 → P4)
   - Each gap: potential, system, missing properties, recommended action

4. Reference Quality Report
   - Percentage with complete literature citations
   - Percentage missing uncertainty
   - Reference value conflict list

5. Trend Analysis (vs. previous report)
   - New verification count
   - Potentials with improved/degraded grades
```

### Most Useful Visualizations

| Visualization Type | Purpose | Priority |
|-------------------|---------|----------|
| **Coverage Matrix Heatmap** | System × properties, color-coded grades, gaps visible at glance | 🔴 Highest |
| **Grade Distribution Pie Chart** | Count and percentage of A/B/C/D/F | 🟡 High |
| **System Coverage Bar Chart** | Verification completion percentage per system | 🟡 High |
| **Gap Trend Line Chart** | Unverified count over time | 🟢 Medium |
| **Potential Comparison Radar Chart** | Multi-property grades for single potential | 🟢 Medium |

### Required Drill-Down Capabilities

1. **System → Potential → Property**:
   - Click heatmap system → list all potentials for that system
   - Click potential → show detailed verification results for all properties
   - Click property → show LAMMPS calculated value vs. reference value vs. deviation

2. **Gap → Reference → Action Recommendation**:
   - Click gap → show why it's a gap (no reference? LAMMPS failed? not submitted?)
   - If missing reference → show Ref-Gap-Fill query results
   - Action recommendation ("need to add reference value", "need to fix potential file", "need to support new pair_style")

### Delivery Mechanisms

| Mechanism | Purpose | Priority |
|----------|---------|----------|
| **Web Dashboard** | Real-time verification status and gap viewing | 🔴 Required (admin backend extension) |
| **API Endpoint** | Programmatic verification data consumption | 🔴 Required |
| **PDF Export** | Formal reports, archiving | 🟡 Important |
| **Email/Message Alerts** | Verification failure notifications | 🟡 Important (not currently implemented) |

---

## 5. Automation vs. Human-in-the-Loop

### Should Automate (High ROI)

1. **Auto-verification trigger**: Automatically submit LAMMPS verification after new/updated potentials, no manual intervention
2. **Batch verification queue**: One-click verify all unverified potentials, background Celery worker sequential execution
3. **Reference value auto-fill** (Ref-Gap-Fill partially implemented): Auto-lookup missing reference values from NFMD database and knowledge base
4. **Auto gap detection**: Periodically scan verifications table, identify unverified potentials and property gaps
5. **Failure auto-retry**: After LAMMPS failure, auto-analyze error type (file missing vs. calculation divergence vs. pair_style incompatibility), auto-fix when possible
6. **Non-EAM potential extension**: Auto-identify pair_style type, select correct LAMMPS template

### Must Keep Human (Critical Judgment)

| Human Judgment Step | Reason |
|--------------------|--------|
| **Reference value entry and review** | Reference values are verification baseline; wrong values pollute all subsequent verification results. Must be reviewed by domain experts for literature source and data quality |
| **Final verification judgment** | A-F grades based on relative error, but "acceptable" depends on specific application scenario. Reactor safety analysis may require B grade or above, while preliminary screening C grade is acceptable |
| **Non-standard potential template selection** | ReaxFF, COMB and other complex potentials' LAMMPS inputs need human determination |
| **Reference value conflict resolution** | Multiple literature sources give different values for same property (e.g., U BCC elastic constants Smirnov vs. 002_seed), need expert judgment on which to choose |
| **New system first verification** | First-time verification of new material systems requires human confirmation of reference value completeness and calculation parameter reasonableness |

---

## 6. Reference Standards Alignment

Lili recommends **aligning with OpenKIM**—the industry's most mature potential verification framework.

**OpenKIM Standards to Adopt:**
- **Standard property set**: lattice, elastic, surface, vacancy, stacking-fault, phonon, ...
- **Difference metrics**: Relative error + absolute error
- **Reference value sources**: Experiment + high-precision DFT, all tagged with literature DOI
- **Verification report templates**: Standardized formats for reproducibility

---

## 7. Current Pain Points

| Pain Point | Severity | Description |
|------------|----------|-------------|
| **Potential file missing** | 🔴 High | 19/65 potentials lack file_url, cannot verify. Mostly Buckingham/Tersoff/AIREBO non-EAM types, need special handling |
| **Reference values incomplete** | 🔴 High | Many materials/structures only have lattice constant reference values, missing elastic constants and vacancy formation energies. No reference values means no grade assignment |
| **Non-EAM potential incompatibility** | 🟡 Medium | Buckingham, Tersoff, AIREBO, ReaxFF potentials need different pair_style, LAMMPS runner currently mainly supports EAM/MEAM |
| **Debugging difficulty after verification failure** | 🟡 Medium | error_log in database, requires manual reading of LAMMPS logs to locate issues (mass mismatch, missing pair_coeff, etc.) |
| **Missing batch operations** | 🟡 Medium | Currently submit verifications one by one, 65 potentials need individual clicks or scripts |
| **Reference value maintenance chaotic** | 🟠 Medium | Reference values split across two tables (material/structure vs element/crystal_structure), seed data has duplicates, source tagging inconsistent |
| **No uncertainty quantification** | 🟠 Medium | Reference values have no uncertainty column, cannot distinguish high-precision experimental values from rough estimates |
| **Missing CI/CD integration** | 🟢 Low | New potentials don't auto-trigger verification, requires manual submission |

---

## 8. Implementation Recommendations

### Immediate Actions (P0)

1. **Add uncertainty column** to reference_values table (biggest data quality gap)
2. **Complete reference values** for P0 systems (U, UO₂, Zr, U-Zr, Fe) - core properties only
3. **Implement coverage matrix heatmap** as first visualization
4. **Auto-gap detection** service to scan and report P0-P1 gaps

### Short-term (P1-P2)

5. **Batch verification queue** for unverified potentials
6. **Reference value auto-fill** integration with Ref-Gap-Fill system
7. **Non-EAM potential support** (Buckingham, Tersoff pair_style detection)
8. **Verification failure auto-retry** with error analysis

### Medium-term (P3-P4)

9. **Extended properties** implementation (surface energy, diffusion coefficient, etc.)
10. **Reference quality audit** system (completeness, uncertainty, DOI coverage)
11. **OpenKIM alignment** for property standards and reporting format
12. **PDF export** and alert notifications for verification failures

---

## Next Steps: CPO Disposition

**NFM-84 Status**: ✅ **COMPLETE** - Consultation objectives achieved

**Deliverables Received**:
- ✅ Summary of verification requirements and gap definition
- ✅ List of priority areas for verification data collection (P0-P4 framework)
- ✅ Stakeholder expectations for reporting format (detailed spec)
- ✅ Current pain points and automation opportunities identified
- ✅ Reference standards recommendation (OpenKIM alignment)

**Follow-up Issues to Create**:
1. **NFM-90**: Implement verification gap detection service (P0-P1 focus)
2. **NFM-91**: Add uncertainty estimates to reference_values schema
3. **NFM-92**: Build coverage matrix heatmap visualization
4. **NFM-93**: Complete reference values for P0 safety-critical systems
5. **NFM-94**: Implement batch verification queue for unverified potentials
6. **NFM-95**: Add non-EAM potential support (Buckingham, Tersoff, AIREBO)

**Blocker Resolution**: NFM-89 (OpenClaw Gateway) still pending, but consultation successfully completed via manual workaround. Future Lili consultations will continue to need manual forwarding until gateway is restored.

---

*Synthesized from Lili's complete consultation response at `/Users/lwj04/Projects/nucpot/docs/VERIFICATION-CONSULTATION.md`*
