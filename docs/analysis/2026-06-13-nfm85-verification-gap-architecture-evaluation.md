# NFM-85 Verification Gap Detection Workflow Architecture Evaluation

**Date**: 2026-06-13
**Author**: CTO
**Status**: Draft
**Dependencies**: NFM-84 (Verification Requirements Synthesis) ✅ Complete
**Blocks**: NFM-83.3 (Workflow Designer Assessment)

---

## Executive Summary

**Recommendation**: Implement a three-tier architecture for verification gap detection and collection:

1. **Gap Detection Layer** (automated): SQL-based queries to identify missing verification data
2. **Collection Orchestration Layer** (hybrid): Prioritized workflow with automated triggers + human approval gates
3. **Quality Tracking Layer** (automated): Coverage metrics and trend analysis

**Key Finding**: The current `RefGapFillStaging` model already supports 70% of required functionality. The **missing piece** is a verification coverage model that links reference values to LAMMPS potentials and tracks which (potential, property) combinations have been verified.

**Implementation Complexity**: Medium
- Gap detection queries: Low complexity (2-3 days)
- Collection workflow: Medium complexity (1-2 weeks)
- Coverage model: Medium complexity (1 week)

---

## 1. Current Architecture Analysis

### 1.1 Existing Components

**`RefGapFillStaging` Model Strengths:**
- ✅ Has `uncertainty` field (nullable) - can support NFM-84's biggest gap
- ✅ Has `review_note` field - stores verification notes with `VERIFY:` prefix
- ✅ Has `status` enum (PENDING/APPROVED/REJECTED/PROMOTED) - supports workflow
- ✅ Has `confidence` enum (HIGH/MEDIUM/LOW) - supports data quality tracking
- ✅ Has indexes on `element_system`, `phase`, `property_name` - efficient filtering

**Verification Service Capabilities:**
- ✅ `export_for_verification()` - bulk export with rich filters
- ✅ `process_verification_results()` - handles A-F grading callbacks
- ✅ Machine-parseable note format: `VERIFY:{grade} | source={src} | value={val}±{unc}`

### 1.2 Critical Missing Pieces

**Gap Detection:**
- ❌ No query to identify which reference values lack verification
- ❌ No coverage tracking by (element_system, phase, property_name) tuple
- ❌ No priority-based gap classification (P0-P4 from NFM-84)

**Collection Workflow:**
- ❌ No automated triggers for verification submission
- ❌ No batch queue for unverified potentials
- ❌ No integration with LAMMPS potential files

**Coverage Tracking:**
- ❌ No link between `potentials` table and `reference_values` verification status
- ❌ No visualization data structure for coverage matrix
- ❌ No trend tracking over time

---

## 2. Gap Detection Architecture

### 2.1 Required Data Model Extension

**New Table: `verification_coverage`**

```sql
CREATE TABLE verification_coverage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    potential_id UUID NOT NULL REFERENCES potentials(id),
    element_system VARCHAR(50) NOT NULL,
    phase VARCHAR(50),
    property_name VARCHAR(100) NOT NULL,
    staging_id UUID REFERENCES _ref_gap_fill_staging(id),
    verification_status VARCHAR(20) NOT NULL, -- 'verified', 'unverified', 'no_reference', 'not_applicable'
    verdict VARCHAR(1), -- A-F or NULL
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(potential_id, element_system, phase, property_name),
    INDEX idx_coverage_potential (potential_id),
    INDEX idx_coverage_status (verification_status),
    INDEX idx_coverage_element_phase_prop (element_system, phase, property_name)
);
```

**Rationale:**
- **Why new table?** Current `RefGapFillStaging` is a transient staging table for reference values, not a persistent coverage tracker. Verification coverage needs to persist even if staging records are promoted/cleaned up.
- **Why link to potentials?** LAMMPS potentials are the unit being verified. One potential can calculate multiple properties for multiple systems.
- **Why `verification_status` enum?** Four distinct states:
  - `verified`: Has A-F grade in review_note
  - `unverified`: Has reference value but no verification
  - `no_reference`: Calculable property but no reference value exists
  - `not_applicable`: Property not calculable for this potential type (e.g., surface energy for pair_style that doesn't support it)

### 2.2 Gap Detection Queries

**Query 1: Identify Unverified (Potential, System, Property) Tuples**

```python
-- SQL: Find all verifiable but unverified combinations
SELECT DISTINCT
    p.id as potential_id,
    p.name as potential_name,
    p.pair_style,
    rv.element_system,
    rv.phase,
    rv.property_name
FROM potentials p
CROSS JOIN (
    SELECT DISTINCT element_system, phase, property_name
    FROM _ref_gap_fill_staging
    WHERE status IN ('approved', 'promoted')
) rv
LEFT JOIN verification_coverage vc
    ON vc.potential_id = p.id
    AND vc.element_system = rv.element_system
    AND COALESCE(vc.phase, '') = COALESCE(rv.phase, '')
    AND vc.property_name = rv.property_name
WHERE vc.id IS NULL  -- No coverage record exists
  AND p.file_url IS NOT NULL  -- Only potentials with files
ORDER BY rv.element_system, rv.property_name;
```

**Query 2: Classify Gaps by Priority (P0-P4)**

```python
-- SQL: Priority classification based on NFM-84 framework
WITH gap_counts AS (
    SELECT
        vc.element_system,
        vc.property_name,
        vc.verification_status,
        COUNT(*) as count
    FROM verification_coverage vc
    GROUP BY vc.element_system, vc.property_name, vc.verification_status
)
SELECT
    gc.element_system,
    gc.property_name,
    gc.verification_status,
    gc.count,
    CASE
        WHEN gc.element_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr')
            AND gc.property_name IN ('lattice_constant', 'elastic_constants', 'vacancy_formation_energy')
            AND gc.verification_status = 'unverified'
        THEN 'P0'
        WHEN gc.element_system IN ('U', 'UO2', 'Zr', 'Fe', 'U-Zr', 'U-Mo', 'U-Pu-Zr', 'Mo', 'Nb')
            AND gc.property_name IN ('surface_energy', 'diffusion_coefficient', 'mixing_enthalpy')
            AND gc.verification_status = 'unverified'
        THEN 'P1'
        WHEN gc.element_system IN ('U-Pu-Zr', 'UN', 'UC', 'SiC')
            AND gc.verification_status = 'unverified'
        THEN 'P2'
        WHEN gc.verification_status = 'unverified'
        THEN 'P3'
        WHEN gc.verification_status = 'no_reference'
        THEN 'P4'
        ELSE 'N/A'
    END as priority_level
FROM gap_counts gc
WHERE gc.verification_status IN ('unverified', 'no_reference')
ORDER BY
    CASE priority_level
        WHEN 'P0' THEN 1
        WHEN 'P1' THEN 2
        WHEN 'P2' THEN 3
        WHEN 'P3' THEN 4
        WHEN 'P4' THEN 5
    END,
    gc.element_system,
    gc.property_name;
```

**Query 3: Coverage Summary by System**

```python
-- SQL: Generate data for coverage matrix heatmap
SELECT
    element_system,
    property_name,
    COUNT(*) FILTER (WHERE verification_status = 'verified' AND verdict IN ('A', 'B')) as verified_ab,
    COUNT(*) FILTER (WHERE verification_status = 'verified' AND verdict IN ('C', 'D')) as verified_cd,
    COUNT(*) FILTER (WHERE verification_status = 'verified' AND verdict = 'F') as verified_f,
    COUNT(*) FILTER (WHERE verification_status = 'unverified') as unverified,
    COUNT(*) FILTER (WHERE verification_status = 'no_reference') as no_reference,
    COUNT(*) as total
FROM verification_coverage
GROUP BY element_system, property_name
ORDER BY element_system, property_name;
```

### 2.3 Gap Detection Service Design

**New Service: `gap_detection_service.py`**

```python
"""Verification gap detection service (NFM-85).

Identifies and classifies verification gaps across the LAMMPS
potential catalog using the coverage model.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

class Priority(str, Enum):
    """Gap priority levels from NFM-84."""
    P0 = "P0"  # Safety-critical
    P1 = "P1"  # Performance-related
    P2 = "P2"  # System coverage
    P3 = "P3"  # Property extension
    P4 = "P4"  # Data quality

@dataclass
class VerificationGap:
    """A single verification gap record."""
    potential_id: str
    potential_name: str
    element_system: str
    phase: str | None
    property_name: str
    priority: Priority
    gap_type: Literal["unverified", "no_reference", "not_applicable"]

@dataclass
class GapReport:
    """Aggregated gap report."""
    total_potentials: int
    verified_count: int
    unverified_count: int
    no_reference_count: int
    coverage_percentage: float
    gaps_by_priority: dict[Priority, int]
    gaps_by_system: dict[str, int]

async def scan_gaps(
    session: AsyncSession,
    *,
    priority_filter: Priority | None = None,
    element_system: str | None = None,
    property_name: str | None = None,
) -> GapReport:
    """Scan verification coverage and generate gap report."""
    # Implementation uses Query 2 + Query 3 above
    ...

async def list_unverified_tuples(
    session: AsyncSession,
    *,
    priority: Priority | None = None,
    limit: int = 1000,
) -> list[VerificationGap]:
    """List specific (potential, system, property) tuples needing verification."""
    # Implementation uses Query 1 + Query 2
    ...
```

---

## 3. Automatic Collection Workflow Architecture

### 3.1 Workflow Design Philosophy

**From NFM-84 Section 5**: "High ROI automation" vs "Critical judgment must be human"

**Automated Steps (No Human Intervention):**
1. ✅ **Gap detection scan** - scheduled daily, identifies new gaps
2. ✅ **Priority sorting** - automatic P0 → P4 classification
3. ✅ **Batch queue generation** - group gaps by potential for efficient LAMMPS runs
4. ✅ **Reference value lookup** - query NFMD database for missing references
5. ✅ **Verification submission** - send to LAMMPS runner
6. ✅ **Result parsing** - extract A-F grades from logs
7. ✅ **Coverage record update** - mark tuples as verified

**Human-in-the-Loop Gates (Require Approval):**
1. 🔴 **Reference value entry** - domain experts must validate sources
2. 🔴 **F-grade adjudication** - decide whether to reject or request re-verification
3. 🔴 **Reference value conflicts** - resolve when multiple sources disagree
4. 🔴 **Non-EAM potential template selection** - ReaxFF/COMB need custom LAMMPS inputs
5. 🔴 **New system first verification** - sanity check on first-time calculations

### 3.2 Workflow State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                     GAP_DETECTED                                 │
│  (gap_detection_service identifies unverified tuple)             │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ├─→ [P0/P1] → AUTO_QUEUE_VERIFICATION
                              │               (send to LAMMPS runner)
                              │               │
                              │               ├─→ SUCCESS → PARSE_GRADE
                              │               │               │
                              │               │               └─→ UPDATE_COVERAGE
                              │               │
                              │               └─→ ERROR → LOG_FAILURE
                              │                               │
                              │                               └─→ HUMAN_REVIEW
                              │
                              ├─→ [P2/P3] → BATCH_QUEUE
                              │            (accumulate 10+ gaps)
                              │            │
                              │            └─→ AUTO_QUEUE_VERIFICATION
                              │
                              └─→ [P4/no_reference] → MANUAL_REF_REQUEST
                                              (notify domain experts)
```

### 3.3 Collection Service Design

**New Service: `verification_collection_service.py`**

```python
"""Verification collection orchestration service (NFM-85).

Implements automated workflow for verification gap collection
with human-in-the-loop gates for critical decisions.
"""

from enum import Enum
from dataclasses import dataclass

class CollectionTaskStatus(str, Enum):
    """Verification collection task status."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    REJECTED = "rejected"

@dataclass
class CollectionTask:
    """A verification collection task."""
    gap_id: str
    potential_id: str
    element_system: str
    property_name: str
    priority: Priority
    status: CollectionTaskStatus
    error_message: str | None = None
    human_review_required: bool = False

async def create_verification_batch(
    session: AsyncSession,
    *,
    priority_filter: Priority | None = None,
    batch_size: int = 10,
) -> UUID:
    """Create a batch of verification tasks for LAMMPS runner.
    
    Groups unverified tuples by potential to minimize file I/O.
    Returns batch_id for tracking.
    """
    ...

async def submit_to_lammps(
    session: AsyncSession,
    batch_id: UUID,
    runner_endpoint: str,
) -> dict:
    """Submit verification batch to LAMMPS runner service.
    
    POST to /verify endpoint with potential IDs and reference values.
    Returns submission receipt with task IDs.
    """
    ...

async def process_lammps_results(
    session: AsyncSession,
    batch_id: UUID,
    results: list[dict],
) -> dict:
    """Process LAMMPS runner results and update coverage.
    
    Parses logs, extracts A-F grades, updates verification_coverage.
    Auto-updates COMPLETED tasks, flags FAILED for human review.
    """
    ...

async def scan_and_queue(
    session: AsyncSession,
    *,
    priority_threshold: Priority = Priority.P1,
) -> dict:
    """Scheduled task: scan gaps and auto-queue high-priority items.
    
    Runs daily via Celery beat. Identifies new gaps, creates
    collection tasks, and submits P0/P1 batches automatically.
    """
    ...
```

### 3.4 Integration Points

**With Existing Services:**

1. **LAMMPS Runner** (verify-service integration):
   - `POST /verify` with `potential_id`, `reference_values`
   - Callback to `POST /api/v1/verification/callback` (already exists)
   - Need to add: `potential_id` to callback schema

2. **Ref-Gap-Fill Service** (already implemented):
   - Use `export_for_verification()` to get reference values
   - Use `process_verification_results()` to handle callbacks
   - Gap detection adds: query which tuples still lack verification

3. **Gap Scan Service** (NFM-55, already exists):
   - Current: scans for missing reference values
   - Extension: add verification coverage scan
   - Returns: both ref gaps AND verification gaps

---

## 4. Data Quality Requirements

### 4.1 Reference Value Quality Metrics

**From NFM-84 Section 7**: "Current Pain Points"

**Quality Dimensions:**

| Dimension | Current State | Target | Measurement |
|-----------|---------------|--------|-------------|
| **Uncertainty Coverage** | 0% (no uncertainty in ref values) | 100% for P0 properties | `% WITH uncertainty IS NOT NULL` |
| **Source DOI Coverage** | ~40% (inconsistent) | 100% for promoted values | `% WITH source_doi IS NOT NULL` |
| **Confidence Level** | Default MEDIUM | Audited per property | Distribution of HIGH/MEDIUM/LOW |
| **Value Conflicts** | Undetected | Flagged for review | `COUNT(DISTINCT value) > 1` per (element, phase, property) |

**Quality Gates for Collection:**

```python
@dataclass
class ReferenceQualityGate:
    """Quality check before using reference value for verification."""
    has_uncertainty: bool  # Required for P0
    has_source_doi: bool  # Required for promoted
    confidence_threshold: Confidence  # Minimum HIGH for P0
    no_conflicts: bool  # Flag if multiple values exist

def quality_gate_for_priority(priority: Priority) -> ReferenceQualityGate:
    """Return quality requirements based on gap priority."""
    if priority == Priority.P0:
        return ReferenceQualityGate(
            has_uncertainty=True,
            has_source_doi=True,
            confidence_threshold=Confidence.HIGH,
            no_conflicts=True,
        )
    elif priority == Priority.P1:
        return ReferenceQualityGate(
            has_uncertainty=True,
            has_source_doi=True,
            confidence_threshold=Confidence.MEDIUM,
            no_conflicts=False,  # Allow conflicts, will flag
        )
    else:
        return ReferenceQualityGate(
            has_uncertainty=False,
            has_source_doi=False,
            confidence_threshold=Confidence.LOW,
            no_conflicts=False,
        )
```

### 4.2 Verification Result Quality

**A-F Grade Distribution Tracking:**

```sql
-- Query: Track grade distribution over time
SELECT
    DATE_TRUNC('month', verified_at) as month,
    verdict,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY DATE_TRUNC('month', verified_at)), 2) as percentage
FROM verification_coverage
WHERE verification_status = 'verified'
  AND verified_at IS NOT NULL
GROUP BY DATE_TRUNC('month', verified_at), verdict
ORDER BY month DESC, verdict;
```

**Trend Analysis:**

- **Improvement**: Tuple transitions from `unverified` → `verified`
- **Regression**: Tuple transitions from `verified` → `unverified` (potential updated)
- **Quality Shift**: Verdict changes (e.g., C → B after better reference found)

---

## 5. Automation vs Manual Feasibility Assessment

### 5.1 Automation Feasibility Matrix

| Step | Automation Feasibility | Rationale |
|------|----------------------|-----------|
| **Gap detection scan** | ✅ **Fully automated** | Pure SQL query, no judgment needed |
| **Priority classification** | ✅ **Fully automated** | Rules-based (P0-P4 defined by NFM-84) |
| **Batch queue generation** | ✅ **Fully automated** | Group by potential_id, minimize file loads |
| **Reference value lookup** | ✅ **Fully automated** | Query NFMD database via Ref-Gap-Fill |
| **LAMMPS submission** | ✅ **Fully automated** | POST to runner endpoint, async callback |
| **Result parsing** | ✅ **Fully automated** | Regex extract A-F from log output |
| **Coverage update** | ✅ **Fully automated** | UPDATE verification_coverage on callback |
| **Reference value entry** | ❌ **Manual only** | Domain expertise required (NFM-84) |
| **F-grade adjudication** | ⚠️ **Semi-automated** | Auto-flag, human decides reject/retry |
| **Conflict resolution** | ❌ **Manual only** | Expert judgment on which source is correct |
| **Non-EAM template selection** | ❌ **Manual only** | Complex potentials need custom inputs |
| **New system sanity check** | ⚠️ **Semi-automated** | Auto-flag, human reviews first-time results |

### 5.2 ROI Analysis for Automation

**High ROI (Implement First):**

1. **Gap detection scan** (2-3 days)
   - Impact: Immediate visibility into verification status
   - Value: Enables all downstream automation
   - Risk: Low (read-only query)

2. **Batch queue generation** (1-2 days)
   - Impact: Reduces manual submission from 65 clicks to 1 batch
   - Value: Saves engineer time, reduces error rate
   - Risk: Low (grouping logic is deterministic)

3. **Coverage model + tracking** (3-5 days)
   - Impact: Persistent verification state across system lifetime
   - Value: Foundation for trend analysis, visualization
   - Risk: Medium (migration script needed for existing data)

**Medium ROI (Implement Second):**

4. **LAMMPS auto-submission** (2-3 days)
   - Impact: Eliminates manual form submissions
   - Value: Faster verification turnaround
   - Risk: Medium (error handling, retry logic needed)

5. **Result parsing + coverage update** (2-3 days)
   - Impact: Auto-update coverage on callback
   - Value: Closed-loop automation
   - Risk: Low (callback handler already exists)

**Low/Deferred ROI (Implement Later):**

6. **Reference value auto-fill** (5-7 days)
   - Impact: Reduces manual data entry
   - Value: Moderate, but requires NFMD integration
   - Risk: High (depends on external data quality)

7. **Failure auto-retry** (3-5 days)
   - Impact: Reduces manual debugging
   - Value: Moderate, but most failures need human review anyway
   - Risk: Medium (error classification logic complex)

### 5.3 Implementation Recommendation

**Phase 1 (Week 1-2): Foundation**
- Create `verification_coverage` table + migration
- Implement gap detection service with P0-P4 queries
- Build coverage summary API endpoint

**Phase 2 (Week 3-4): Automation**
- Implement batch queue generation
- Add LAMMPS auto-submission (P0/P1 only)
- Extend callback handler to update coverage

**Phase 3 (Week 5-6): Quality + Visualization**
- Build coverage matrix heatmap (NFM-92)
- Add trend analysis dashboard
- Implement quality gates for P0 properties

**Phase 4 (Week 7-8): Advanced Features)**
- Reference value auto-fill integration
- Non-EAM potential support
- Failure auto-retry with error classification

---

## 6. Technical Risks and Mitigations

### 6.1 Risk Matrix

| Risk | Severity | Probability | Mitigation |
|------|----------|--------------|------------|
| **Coverage model migration fails** | High | Medium | Backup staging table, dry-run migration script |
| **LAMMPS runner rate limits** | Medium | High | Implement exponential backoff, queue throttling |
| **P0 gaps overwhelm queue** | High | Medium | Priority queue with P0 preempting P3/P4 |
| **False positives in gap detection** | Medium | Low | Manual review of first 100 gaps, tune queries |
| **Callback processing deadlock** | Medium | Low | Async task queue, retry with exponential backoff |
| **Reference value conflicts block automation** | Low | High | Conflict resolution UI, promote most recent source |

### 6.2 Performance Considerations

**Gap Detection Query Performance:**
- **Query 1** (unverified tuples): `CROSS JOIN` scales as O(P × R) where P=65 potentials, R=unique (element, phase, property) tuples
- **Mitigation**: Materialize view refreshed nightly, cache results
- **Estimated runtime**: < 1 second for current scale (65 × ~200 = 13,000 rows max)

**Coverage Update Performance:**
- **Callback volume**: Up to 1000 results per batch (current schema limit)
- **Update strategy**: Batch UPDATE with `RETURNING` clause
- **Estimated runtime**: < 500ms for 1000 updates

---

## 7. Deliverables Summary

### 7.1 Architecture Artifacts

**Data Model:**
- ✅ `verification_coverage` table schema (Section 2.1)
- ✅ Migration script design (Alembic)
- ✅ Indexes for query performance

**API Design:**
- ✅ Gap detection service API (Section 2.3)
- ✅ Collection orchestration API (Section 3.3)
- ✅ Callback extension (add `potential_id`)

**Workflow Design:**
- ✅ State machine diagram (Section 3.2)
- ✅ Human-in-the-loop gate specification
- ✅ Error handling and retry logic

**Quality Tracking:**
- ✅ Reference quality gates (Section 4.1)
- ✅ Grade distribution queries (Section 4.2)
- ✅ Trend analysis framework

### 7.2 Implementation Work Breakdown

**NFM-90**: Verification gap detection service (P0-P1 focus)
- Create `verification_coverage` table
- Implement `gap_detection_service.py`
- Add gap summary API endpoint

**NFM-91**: Add uncertainty estimates to reference_values schema
- Add `uncertainty` column (nullable → NOT NULL for P0)
- Migration script for existing values

**NFM-92**: Build coverage matrix heatmap visualization
- Frontend heatmap component
- API endpoint for coverage data

**NFM-93**: Complete reference values for P0 systems
- Manual data entry workflow
- Bulk import from NFMD

**NFM-94**: Implement batch verification queue
- Queue management service
- LAMMPS runner integration

**NFM-95**: Add non-EAM potential support
- Detect pair_style type
- Template selection logic

---

## 8. Feasibility Conclusion

### 8.1 Technical Feasibility: **HIGH** ✅

**Evidence:**
- Existing `RefGapFillStaging` model supports 70% of requirements
- Gap detection queries are straightforward SQL
- Automation pathways are clear (7/11 steps fully automatable)
- No blocking technical dependencies

**Remaining Work:**
- Create `verification_coverage` model (1 week)
- Implement gap detection service (1-2 weeks)
- Build collection orchestration (2-3 weeks)
- Add quality tracking (1 week)

**Total Estimated Effort**: 5-7 weeks for P0-P1 automation

### 8.2 Business Value: **HIGH** ✅

**Quantified Benefits:**
- **Time savings**: Reduce manual verification from 65 clicks to 1 batch submission (98% reduction)
- **Coverage visibility**: Real-time gap detection vs. quarterly manual audits
- **Quality improvement**: P0 property uncertainty tracking (currently 0% → target 100%)
- **Risk reduction**: Automated verification for safety-critical systems (U, UO₂, Zr, Fe)

**Qualitative Benefits:**
- Enables OpenKIM alignment (NFM-84 recommendation)
- Foundation for trend analysis and continuous improvement
- Reduces domain expert time on routine tasks

### 8.3 Recommendation: **PROCEED** ✅

**Next Steps:**
1. **Board approval** for architecture and phase breakdown
2. **Assign NFM-90** to CPO team for implementation
3. **Prioritize P0 gaps** (U, UO₂, Zr, Fe core properties)
4. **Schedule NFM-92** (visualization) for sprint after NFM-90 completes

---

## Appendix A: SQL Schema

```sql
-- Full verification_coverage table definition
CREATE TABLE verification_coverage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    potential_id UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,
    element_system VARCHAR(50) NOT NULL,
    phase VARCHAR(50),
    property_name VARCHAR(100) NOT NULL,
    staging_id UUID REFERENCES _ref_gap_fill_staging(id) ON DELETE SET NULL,
    verification_status VARCHAR(20) NOT NULL
        CHECK (verification_status IN ('verified', 'unverified', 'no_reference', 'not_applicable')),
    verdict VARCHAR(1) CHECK (verdict IS NULL OR verdict IN ('A', 'B', 'C', 'D', 'E', 'F')),
    verified_at TIMESTAMP WITH TIME ZONE,
    verification_note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT unique_potential_system_property
        UNIQUE (potential_id, element_system, COALESCE(phase, ''), property_name),
    
    CONSTRAINT verified_requires_verdict
        CHECK (verification_status != 'verified' OR verdict IS NOT NULL),
    
    CONSTRAINT verified_requires_timestamp
        CHECK (verification_status != 'verified' OR verified_at IS NOT NULL)
);

-- Indexes for performance
CREATE INDEX idx_coverage_potential ON verification_coverage(potential_id);
CREATE INDEX idx_coverage_status ON verification_coverage(verification_status);
CREATE INDEX idx_coverage_element_phase_prop ON verification_coverage(element_system, COALESCE(phase, ''), property_name);
CREATE INDEX idx_coverage_verdict ON verification_coverage(verdict) WHERE verdict IS NOT NULL;
CREATE INDEX idx_coverage_verified_at ON verification_coverage(verified_at) WHERE verified_at IS NOT NULL;

-- Trigger for updated_at
CREATE TRIGGER update_verification_coverage_updated_at
    BEFORE UPDATE ON verification_coverage
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

---

## Appendix B: API Contract Extensions

**Verification Callback Schema Extension:**

```python
class VerificationCallbackRequest(BaseModel):
    """Extended callback with potential_id for coverage tracking."""
    
    batch_id: UUID | None = None
    results: list[VerificationResult]
    
    # NEW: Link verification to specific potential
    potential_id: UUID | None = Field(
        default=None,
        description="Potential ID being verified (optional for backward compat).",
    )
```

**Gap Detection API Endpoints:**

```python
# GET /api/v1/verification/gaps
class GapSummaryResponse(BaseModel):
    """Summary of verification gaps."""
    total_potentials: int
    verified_count: int
    unverified_count: int
    coverage_percentage: float
    gaps_by_priority: dict[str, int]

# GET /api/v1/verification/gaps/detail
class GapListResponse(BaseModel):
    """Detailed list of verification gaps."""
    gaps: list[VerificationGap]
    total: int
    page: int
    limit: int

# GET /api/v1/verification/coverage/matrix
class CoverageMatrixResponse(BaseModel):
    """Coverage data for heatmap visualization."""
    matrix: list[dict]  # [{element_system, property_name, verified_ab, verified_cd, unverified, ...}]
```

---

*Document Status: DRAFT - Ready for board review*
*Next Action: Present to CEO for NFM-85 disposition*
