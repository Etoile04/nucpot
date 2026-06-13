---
name: nfm88-verification-consultation
description: NFM-88 consultation with Lili on nucpot verification requirements
metadata:
  type: project
---

# NFM-88 Verification Requirements Consultation

## Status
**State:** ✅ Completed  
**Issue:** NFM-88  
**Date Initiated:** 2026-06-13  
**Date Completed:** 2026-06-13  
**Outcome:** Successful consultation with comprehensive response received and processed

## Consultation Scope

Domain expert consultation with Lili (NFMD Coach) to understand:

1. **Missing verification data** - What verification data is currently missing from nucpot database
2. **Verification gap definition** - What constitutes a "verification gap" in NFMD context
3. **Current processes** - Manual verification processes and pain points
4. **Stakeholder requirements** - Requirements for verification quality and coverage
5. **Reporting preferences** - Expected format and frequency of verification gap reports
6. **Priority areas** - High-priority areas for verification data collection

## Consultation Questions

Comprehensive questions documented in `docs/consultations/nfm84-lili-consultation-questions.md`:
- 27 detailed questions across 6 sections
- Covers current state, gap definition, processes, requirements, reporting, and priorities
- Ready for domain expert input

## Deliverables Received ✅

All consultation objectives achieved with comprehensive response from Lili:

1. **Verification Requirements Summary** ✅
   - P0-P4 priority framework established
   - Three-layer verification gap definition
   - Safety-critical systems identified (U, UO₂, Zr, U-Zr, Fe)

2. **Stakeholder Requirements Document** ✅
   - Quality thresholds by use case (A-F grading)
   - Coverage requirements (minimum 3/5 core properties)
   - Uncertainty tolerance levels defined

3. **Current Process Analysis** ✅
   - 5 core properties automated with LAMMPS
   - 42/65 potentials verifiable, 16/19 recent successful
   - Pain points identified (19 potentials missing files, reference values incomplete)

4. **Reporting Format Specification** ✅
   - Coverage matrix heatmap (highest priority)
   - Grade distribution visualization
   - Drill-down capabilities (System → Potential → Property)
   - API/web dashboard delivery mechanisms

## Key Findings

**Critical Gaps Identified:**
- **No uncertainty estimates** in reference values — biggest data quality gap
- 19 potentials lack files for verification (non-EAM types)
- Many materials missing elastic constants and vacancy formation energies
- Important systems (U-Pu-Zr, UN, UC, SiC) completely lack reference values

**Verification Gap Definition:**
```
Verification Gap = Σ(
  Unverified Potential,
  Uncovered Property,  
  Missing Reference
)
```

**Priority Framework:**
- P0: Safety-critical gaps (core systems missing core properties)
- P1: Performance gaps (core systems missing secondary properties)
- P2: System coverage gaps (important systems missing verification)
- P3: Property extension gaps (all systems missing extended properties)
- P4: Data quality gaps (incomplete metadata, missing uncertainty)

## Integration Points

- **NFM-70** - Reference value promotion service
- **NFM-77** - Quality gate implementation
- **NFM-84** - Consultation framework
- Future technical implementation issues for verification gap detection and reporting

## Next Steps & Follow-up Implementation

**Follow-up Issues Identified:**
- **NFM-90**: Verification gap detection service (P0-P1 focus)
- **NFM-91**: Add uncertainty estimates to reference_values schema
- **NFM-92**: Coverage matrix heatmap visualization  
- **NFM-93**: Complete reference values for P0 safety-critical systems
- **NFM-94**: Batch verification queue for unverified potentials
- **NFM-95**: Non-EAM potential support (Buckingham, Tersoff, AIREBO)

**Implementation Priority:**
1. **Immediate (P0)**: Add uncertainty column, complete P0 reference values, implement coverage matrix
2. **Short-term (P1-P2)**: Batch verification, reference auto-fill, non-EAM support
3. **Medium-term (P3-P4)**: Extended properties, quality audit, OpenKIM alignment

## Documentation

**Complete Analysis:** `docs/analysis/2026-06-13-nfm84-verification-requirements-synthesis.md`

**Lili's Full Response:** `/Users/lwj04/Projects/nucpot/docs/VERIFICATION-CONSULTATION.md`

## Protocol Note

Manual relay mode active due to OpenClaw/Paperclip protocol mismatch. Workaround tested and functioning correctly.