# NFM-84 Follow-up Implementation Issues

**Created from**: NFM-84 verification consultation deliverables  
**Date**: 2026-06-13  
**Status**: Ready for Lead Engineer assignment

---

## NFM-90: Verification Gap Detection Service (P0-P1)

**Priority**: High (P0 - Safety-critical)  
**Type**: Feature Implementation  
**Estimated effort**: 3-5 days

### Task Description

Implement an automated service to detect and report verification gaps in the NucPot database, focusing on P0 (safety-critical) and P1 (high-priority) gaps.

### Gap Definition Framework

Based on Lili's consultation, verification gaps are defined as:

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
| P0 Critical | Safety-Critical Gap | Core systems (U, UO₂, Zr, U-Zr, Fe) missing core properties | Affects reactor safety analysis |
| P1 High | Performance Gap | Core systems missing secondary properties | Affects radiation damage modeling |
| P2 Medium | System Coverage Gap | Non-core systems (U-Pu-Zr, UN, UC, SiC) missing verification | Affects advanced fuel research |
| P3 Low | Property Extension Gap | All systems missing extended properties | Affects comprehensive assessment |
| P4 Info | Data Quality Gap | Missing uncertainty estimates, incomplete source tags | Affects user confidence |

### P0 Safety-Critical Systems

- **U (BCC)** — lattice constant, elastic constants, vacancy formation energy
- **UO₂ (fluorite)** — lattice constant, elastic constants, vacancy formation energy
- **Zr (HCP)** — lattice constant, elastic constants, vacancy formation energy
- **U-Zr (BCC)** — lattice constant, elastic constants
- **Fe (BCC)** — lattice constant, elastic constants, vacancy formation energy

### Technical Requirements

1. **Gap Detection Service** (`apps/api/src/nfm_db/services/gap_detection_service.py`)
   - Scan `verifications` table for unverified potentials
   - Identify property coverage gaps for verified potentials
   - Cross-reference with `reference_values` table to find missing reference values
   - Assign P0-P4 priority based on system and property criticality
   - Generate gap summary reports

2. **API Endpoints** (`apps/api/src/nfm_db/api/v1/gaps.py`)
   - `GET /api/v1/gaps` - List all gaps with filtering
   - `GET /api/v1/gaps/summary` - Gap statistics
   - `GET /api/v1/gaps/p0-critical` - P0 safety-critical gaps only

3. **Database Schema** (`apps/api/src/nfm_db/models/gap.py`)
   ```python
   class VerificationGap(Base):
       id: UUID
       potential_id: UUID
       system: str  # U, UO2, Zr, etc.
       gap_type: str  # unverified_potential | uncovered_property | missing_reference
       property_name: Optional[str]
       priority: str  # P0 | P1 | P2 | P3 | P4
       created_at: datetime
       resolved_at: Optional[datetime]
   ```

4. **Scheduled Detection** (Celery task)
   - Periodically scan for gaps (daily or weekly)
   - Update gap records automatically
   - Send alerts for new P0 gaps

### Deliverables

- ✅ Gap detection service with P0-P4 classification
- ✅ API endpoints for gap querying
- ✅ Database schema for gap records
- ✅ Scheduled gap detection (Celery task)
- ✅ API tests for gap endpoints
- ✅ Integration tests with verification data

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- NFM-91 (uncertainty estimates) - adds P4 data quality gap detection

### References

- Synthesis: `docs/analysis/2026-06-13-nfm84-verification-requirements-synthesis.md`
- Lili's response: `/Users/lwj04/Projects/nucpot/docs/VERIFICATION-CONSULTATION.md`

---

## NFM-91: Add Uncertainty Estimates to Reference Values

**Priority**: High (P0 - Biggest data quality gap)  
**Type**: Database Schema Extension  
**Estimated effort**: 2-3 days

### Task Description

Add uncertainty column to the `reference_values` table. This is identified as **the single biggest data quality gap** in the current verification system.

### Problem Statement

Currently, reference values in the database have no uncertainty estimates. This means:
- Cannot distinguish between high-precision experimental values and rough estimates
- Safety analysts requiring ±2% uncertainty cannot filter appropriately
- Verification grades don't reflect reference value reliability
- Stakeholder confidence in verification results is reduced

### Technical Requirements

1. **Database Migration** (`apps/api/src/nfm_db/migrations/`)
   - Add `uncertainty` column to `reference_values` table
   - Add `uncertainty_unit` column (%, eV, Å, GPa, etc.)
   - Add `confidence_level` column (1σ, 2σ, 3σ, or descriptive: high/medium/low)
   - Backfill existing records with `null` for uncertainty fields

2. **Schema Updates** (`apps/api/src/nfm_db/schemas/`)
   - Update `ReferenceValue` schema to include uncertainty fields
   - Add validation for uncertainty values (must be non-negative when present)

3. **API Updates** (`apps/api/src/nfm_db/api/v1/reference_values.py`)
   - Update CRUD endpoints to handle uncertainty fields
   - Add filtering by uncertainty presence/absence
   - Add validation for uncertainty values

4. **Data Quality Reports** (extends gap detection)
   - Report percentage of reference values with uncertainty estimates
   - Flag reference values missing uncertainty in critical systems (P0)

### Deliverables

- ✅ Database migration with uncertainty columns
- ✅ Updated schemas with uncertainty fields
- ✅ API endpoints with uncertainty support
- ✅ Data quality report for uncertainty coverage
- ✅ API tests for uncertainty endpoints
- ✅ Migration rollback script

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- Current database schema in `apps/api/src/nfm_db/models/`

### Stakeholder Impact

| Stakeholder | Impact |
|------------|--------|
| Safety Analysts | Can filter by ±2% uncertainty for A-grade verification |
| Researchers | Can distinguish high-precision vs. estimated reference values |
| Database Maintainers | Better data quality tracking |

---

## NFM-92: Coverage Matrix Heatmap Visualization

**Priority**: Medium (P1 - High-value visualization)  
**Type**: Frontend Feature  
**Estimated effort**: 3-4 days

### Task Description

Build the coverage matrix heatmap visualization identified as the **highest priority visualization** in Lili's consultation.

### Visualization Requirements

**Coverage Matrix Heatmap** (system × properties):
- Rows: Elements/systems (U, UO₂, Zr, Fe, U-Zr, etc.)
- Columns: Properties (lattice_constant, C11, C12, C44, E_vac, etc.)
- Cells: Grade (A-F) color-coded, or "—" (unverified), or "?" (no reference)

**Color Coding**:
- A: Green (≤2% deviation)
- B: Light green (≤5%)
- C: Yellow (≤10%)
- D: Orange (≤20%)
- F: Red (>20%)
- Unverified: Gray
- No reference: Light gray

### Technical Requirements

1. **API Endpoint** (`apps/api/src/nfm_db/api/v1/visualization.py`)
   - `GET /api/v1/visualization/coverage-matrix` - Returns matrix data
   - Response format:
     ```json
     {
       "systems": ["U", "UO2", "Zr", "Fe"],
       "properties": ["lattice_constant", "C11", "C12", "E_vac"],
       "matrix": [
         {"system": "U", "property": "lattice_constant", "grade": "A", "value": 3.52, "reference": 3.50},
         {"system": "U", "property": "C11", "grade": null, "value": null, "reference": null},
         ...
       ]
     }
     ```

2. **Frontend Component** (`apps/web/src/components/admin/verification/`)
   - `CoverageMatrix.tsx` - Heatmap visualization
   - Use heatmap library (e.g., `react-heatmap-grid` or D3.js)
   - Interactive tooltips showing detailed values
   - Color legend for grades

3. **Drill-down Functionality**
   - Click cell → show potential details
   - Click potential → show all properties
   - Click property → show LAMMPS vs. reference comparison

### Deliverables

- ✅ API endpoint for coverage matrix data
- ✅ Heatmap visualization component
- ✅ Interactive tooltips and drill-down
- ✅ Color legend for grades
- ✅ Responsive design for different screen sizes
- ✅ E2E tests for heatmap interactions

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- Current verification data in Supabase
- Frontend framework (Vue/Nuxt or Next.js)

### References

- Consultation synthesis: Section 5 (Reporting Format)

---

## NFM-93: Complete P0 Safety-Critical System Reference Values

**Priority**: High (P0 - Safety-critical data)  
**Type**: Data Entry & Validation  
**Estimated effort**: 5-7 days (including literature research)

### Task Description

Complete reference values for P0 safety-critical systems identified in the consultation. **Important systems (U-Pu-Zr, UN, UC, SiC) currently have no reference values at all.**

### P0 Safety-Critical Systems

**Must Complete (5 core systems):**

1. **U (BCC)**
   - Lattice constant
   - Elastic constants (C11, C12, C44)
   - Vacancy formation energy

2. **UO₂ (fluorite)**
   - Lattice constant
   - Elastic constants
   - Vacancy formation energy

3. **Zr (HCP)**
   - Lattice constant
   - Elastic constants
   - Vacancy formation energy

4. **U-Zr (BCC)**
   - Lattice constant
   - Elastic constants

5. **Fe (BCC)**
   - Lattice constant
   - Elastic constants
   - Vacancy formation energy

**Missing Systems (Critical Gap):**

6. **U-Pu-Zr** - No reference values
7. **UN** - No reference values
8. **UC** - No reference values
9. **SiC** - No reference values

### Data Requirements

For each reference value, provide:
- Property value (with units)
- Uncertainty estimate (%)
- Source type (experimental | DFT | review | estimated)
- Literature citation (DOI if available)
- Confidence level (high | medium | low)
- Temperature/pressure conditions (if applicable)

### Technical Requirements

1. **Literature Research**
   - Search NIST IPR, OpenKIM, Materials Project
   - Review key papers for each system
   - Extract experimental/DFT benchmark values

2. **Data Entry Workflow**
   - Admin interface for adding reference values
   - Validation for required fields
   - Source citation management
   - Bulk import CSV capability

3. **Quality Assurance**
   - Cross-check with OpenKIM where available
   - Flag conflicting values from different sources
   - Expert review for P0 systems (Lili approval)

### Deliverables

- ✅ Complete reference values for 5 core P0 systems (all core properties)
- ✅ Initial reference values for 4 missing systems (U-Pu-Zr, UN, UC, SiC)
- ✅ Literature citations with DOIs
- ✅ Uncertainty estimates for all values
- ✅ Data entry workflow with validation
- ✅ Expert sign-off on P0 values

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- NFM-91 (uncertainty estimates) - schema must support uncertainty
- Domain expert review (Lili)

### Stakeholder Impact

| Stakeholder | Impact |
|------------|--------|
| Safety Analysts | P0 systems now have verification baseline |
| Reactor Engineers | Can validate potentials for fuel performance modeling |
| Researchers | Complete reference dataset for core nuclear materials |

---

## NFM-94: Batch Verification Queue for Unverified Potentials

**Priority**: Medium (P1 - Automation efficiency)  
**Type**: Backend Feature  
**Estimated effort**: 3-4 days

### Task Description

Implement batch verification queue to automate verification of multiple unverified potentials. Currently, verifications must be submitted one-by-one (65 potentials = 65 manual submissions).

### Problem Statement

Current workflow is inefficient:
- Manual submission for each potential
- No queue management for concurrent verifications
- No progress tracking for batch operations
- No failure handling and retry logic

### Technical Requirements

1. **Celery Task Queue** (`apps/api/src/nfm_db/tasks/verification_tasks.py`)
   ```python
   @celery.task
   def batch_verify_potentials(potential_ids: List[UUID]):
       for potential_id in potential_ids:
           verify_potential.delay(potential_id)
   ```

2. **Queue Management** (`apps/api/src/nfm_db/services/verification_queue.py`)
   - Add potentials to verification queue
   - Track queue status (pending, running, completed, failed)
   - Handle queue concurrency (max N parallel LAMMPS runs)
   - Queue priority (P0 systems first)

3. **API Endpoints** (`apps/api/src/nfm_db/api/v1/verification_queue.py`)
   - `POST /api/v1/verification-queue/batch` - Submit batch verification
   - `GET /api/v1/verification-queue/status` - Queue status
   - `GET /api/v1/verification-queue/results` - Batch results
   - `POST /api/v1/verification-queue/retry-failed` - Retry failed verifications

4. **Failure Handling**
   - Auto-retry for transient failures
   - Error categorization (file missing vs. calculation divergence vs. pair_style incompatibility)
   - Error reporting with actionable messages

### Deliverables

- ✅ Celery task queue for batch verification
- ✅ Queue management service
- ✅ API endpoints for batch operations
- ✅ Queue status tracking
- ✅ Failure auto-retry logic
- ✅ Error categorization and reporting
- ✅ API tests for queue endpoints
- ✅ Integration tests with LAMMPS runner

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- Current verification service: `apps/api/src/nfm_db/services/verification_service.py`
- Celery infrastructure

### Efficiency Gains

| Metric | Before | After |
|--------|--------|-------|
| 65 potentials verification time | 65 manual submissions (hours) | 1 batch job (minutes) |
| Progress tracking | Manual check each | Real-time queue status |
| Failure handling | Manual resubmit | Auto-retry + categorization |

---

## NFM-95: Non-EAM Potential Support

**Priority**: Medium (P2 - System coverage)  
**Type**: Backend Feature  
**Estimated effort**: 4-5 days

### Task Description

Add support for non-EAM potentials (Buckingham, Tersoff, AIREBO, ReaxFF). Currently, 19/65 potentials cannot be verified due to missing file support (non-EAM types).

### Problem Statement

Current LAMMPS runner only supports EAM/MEAM pair styles:
- 19 potentials lack file_url for verification
- Buckingham, Tersoff, AIREBO potentials need different pair_style
- No automatic pair_style detection
- No LAMMPS template for non-EAM potentials

### Technical Requirements

1. **Pair Style Detection** (`apps/api/src/nfm_db/services/pair_style_detector.py`)
   - Analyze potential file header
   - Detect pair_style type: EAM, MEAM, Buckingham, Tersoff, AIREBO, ReaxFF
   - Cache detection result in database

2. **LAMMPS Templates** (`apps/api/templates/lammps/`)
   - Create template for Buckingham potentials
   - Create template for Tersoff potentials
   - Create template for AIREBO potentials
   - Create template for ReaxFF potentials (if needed)

3. **Template Selection Logic**
   ```python
   PAIR_STYLE_TEMPLATE_MAP = {
       "eam": "eam_template.lammps",
       "meam": "meam_template.lammps",
       "buckingham": "buckingham_template.lammps",
       "tersoff": "tersoff_template.lammps",
       "airebo": "airebo_template.lammps",
   }
   ```

4. **Validation & Testing**
   - Test each template with sample potential
   - Verify LAMMPS runs successfully
   - Check output parsing for each pair_style

### Deliverables

- ✅ Pair style detection service
- ✅ LAMMPS templates for Buckingham, Tersoff, AIREBO
- ✅ Template selection logic
   - ✅ Validation tests for each pair_style
- ✅ Integration tests with LAMMPS runner
- ✅ Documentation for non-EAM potential submission

### Dependencies

- NFM-84 (verification requirements) - ✅ Complete
- Current LAMMPS runner: `apps/api/src/nfm_db/services/verification_service.py`
- LAMMPS templates directory

### Coverage Impact

| Metric | Before | After |
|--------|--------|-------|
| Verifiable potentials | 42/65 (65%) | 61/65 (94%) |
| Unverified due to missing files | 19 | 0 |
| Supported pair_styles | EAM, MEAM | EAM, MEAM, Buckingham, Tersoff, AIREBO |

---

## Issue Creation Summary

**Total Issues**: 6  
**Total Estimated Effort**: 20-28 days

### Priority Breakdown

- **P0 (Safety-Critical)**: 3 issues (NFM-90, NFM-91, NFM-93)
- **P1 (High)**: 1 issue (NFM-92)
- **P2 (Medium)**: 2 issues (NFM-94, NFM-95)

### Dependencies

```
NFM-84 ✅ COMPLETE
    ↓
    ├─→ NFM-91 (uncertainty schema) ──→ NFM-93 (P0 reference values)
    │
    ├─→ NFM-90 (gap detection) ──→ NFM-92 (visualization)
    │
    └─→ NFM-94 (batch queue) + NFM-95 (non-EAM support)
```

### Assignment Recommendation

- **Lead Engineer**: Implement all 6 issues
- **Lili (NFMD Coach)**: Review and approve NFM-93 P0 reference values
- **CPO**: Prioritize P0 issues (NFM-90, NFM-91, NFM-93) for immediate implementation

---

*Document created from NFM-84 consultation deliverables*  
*Date: 2026-06-13*
