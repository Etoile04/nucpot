# NFM-86: Verification Gap Collection Process Design

**Date**: 2026-06-13
**Author**: Workflow Designer
**Status**: Draft for Board Review
**Dependencies**: 
- NFM-84 (Verification Requirements Synthesis) ✅ Complete
- NFM-85 (CTO Architecture Evaluation) ✅ Complete
**Blocks**: NFM-83.4 (Data Expert Hiring Decision)

---

## Executive Summary

**Deliverable**: Complete operational workflow design for verification gap identification, collection, reporting, and maintenance.

**Key Design Decisions**:

1. **Two-Track Workflow**: Separate automated gap detection from manual verification entry
2. **Priority-Based Routing**: P0/P1 gaps trigger immediate notifications; P2/P3 batch weekly; P4 informational only
3. **Stakeholder Notification Matrix**: Targeted alerts by priority and role (safety analysts get P0, researchers get P2-P3)
4. **Weekly Reporting Cadence**: Automated gap summary reports every Monday 8am PT
5. **Quarterly Quality Reviews**: Domain expert audit cycle for P0 reference values

**Process Design Principles**:
- **High ROI automation**: Automate detection, classification, routing
- **Human judgment gates**: Domain expertise required for reference entry, conflict resolution, F-grade adjudication
- **Right-sized notifications**: Alert fatigue prevention via priority-based filtering
- **Sustainable maintenance**: Built-in quality feedback loops

---

## Part 1: Verification Gap Reporting Workflow

### 1.1 User-Facing Gap Discovery Process

**Primary Actors**: 
- Safety Analysts (consume P0 verification status)
- Researchers (consume P1-P3 verification data)
- Database Maintainers (monitor overall coverage)

**Workflow Steps**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: ACCESS GAP DASHBOARD                                              │
│ URL: /admin/verification/gaps                                             │
│ Actors: All stakeholders                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ├─→ View default: P0 safety-critical gaps
                                    │   (U, UO₂, Zr, Fe, U-Zr core properties)
                                    │
                                    └─→ Filter by: priority, system, property, date

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: INTERACTIVE COVERAGE MATRIX                                     │
│ URL: /admin/verification/coverage-matrix                                 │
│ Actors: All stakeholders                                                  │
│ Visualization: Heatmap (systems × properties) with A-F grades            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ├─→ Click cell → view verification details
                                    │   - LAMMPS value vs reference
                                    │   - Deviation percentage
                                    │   - Source DOI
                                    │   - Uncertainty estimate
                                    │
                                    └─→ Download: CSV export for offline analysis

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: DRILL-DOWN TO SPECIFIC GAP                                       │
│ URL: /admin/verification/gaps/{gap_id}                                    │
│ Actors: Safety Analysts, Researchers                                     │
│ Context: Full gap record with action history                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ├─→ For P0 gaps: "Request Verification" button
                                    │   → Auto-queues for next LAMMPS batch
                                    │   → Notifies domain expert of pending review
                                    │
                                    ├─→ For gaps with no reference: "Suggest Reference" button
                                    │   → Opens reference submission form
                                    │   → Flags for domain expert review
                                    │
                                    └─→ For verified gaps: "View Verification History" button
                                        → Shows all A-F grades with timestamps
```

### 1.2 On-Demand Gap Report Generation

**Endpoint**: `POST /api/v1/gaps/report`

**Request Schema**:
```json
{
  "filters": {
    "priority": ["P0", "P1"],
    "element_system": ["U", "UO2", "Zr"],
    "property_name": ["lattice_constant", "elastic_constants"],
    "date_range": {"start": "2026-01-01", "end": "2026-06-13"}
  },
  "format": "csv | json | xlsx",
  "include_details": true
}
```

**Response**:
```json
{
  "report_id": "uuid",
  "status": "processing | completed | failed",
  "download_url": "https://...",
  "expires_at": "2026-06-20T08:00:00Z"
}
```

**Workflow**:
1. User selects filters → clicks "Generate Report"
2. Backend queues Celery task for report generation
3. User receives email when report ready
4. Download link expires after 7 days
5. Report includes: gap counts, priority breakdown, coverage % trend

### 1.3 Gap Report Template (CSV Export)

**Columns**:
| gap_id | priority | potential_id | potential_name | element_system | phase | property_name | gap_type | reference_value | uncertainty | source_doi | created_at | status |
|--------|----------|--------------|----------------|----------------|-------|---------------|----------|----------------|------------|-----------|------------|------------|--------|

**Example Row**:
```
gap_abc123,P0,potential_xyz,EAM_U_UO3_Zr,BCC,U,lattice_constant,unverified,3.524,±0.002,https://doi.org/10.1234/example,2026-06-13T08:00:00Z,pending_verification
```

---

## Part 2: Automatic Verification Data Collection Triggers

### 2.1 Scheduled Gap Detection Workflow

**Cadence**: Daily 6am PT (before business hours)

**Workflow Diagram**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ DAILY 6AM PT: GAP SCAN SCHEDULED TASK                                   │
│ Implementation: Celery beat                                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ├─→ Query 1: Scan for new unverified tuples
                                    │   (potentials × systems × properties)
                                    │
                                    ├─→ Query 2: Classify gaps by priority (P0-P4)
                                    │   (NFM-85 Section 2.2)
                                    │
                                    └─→ Query 3: Generate coverage summary
                                        (for trend tracking)

┌─────────────────────────────────────────────────────────────────────────┐
│ ROUTING BY PRIORITY LEVEL                                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
    [P0, P1]                    [P2, P3]                     [P4]
   (Immediate)                (Batch Weekly)              (Notify Only)
        │                           │                           │
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ AUTO-QUEUE    │         │ ACCUMULATE    │         │ LOG + NOTIFY  │
│ LAMMPS BATCH  │         │ BATCH OF 10+  │         │ DATA TEAM     │
│ (Same Day)    │         │ GAPS          │         │ (Info Only)   │
└───────────────┘         └───────────────┘         └───────────────┘
        │                           │                           │
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ NOTIFY:       │         │ MONDAY 8AM:   │         │ WEEKLY REPORT │
│ Safety        │         │ PROCESS BATCH │         │ INCLUDES:      │
│ Analysts      │         │ VIA LAMMPS    │         │ P4 COUNTS     │
│ (Email/SMS)   │         │ RUNNER        │         │               │
└───────────────┘         └───────────────┘         └───────────────┘
```

### 2.2 Priority-Based Routing Logic

**P0 (Safety-Critical) - Immediate Action**:
- **Trigger**: New gap detected for U, UO₂, Zr, Fe, U-Zr core properties
- **Action**: 
  1. Auto-queue for next LAMMPS batch (within 4 hours)
  2. Send page to Safety Analysts
  3. Create Jira ticket for tracking
  4. Log to P0 Gap Audit Trail
- **SLA**: Verification complete within 24 hours

**P1 (High Priority) - Same-Day Action**:
- **Trigger**: Core systems secondary properties
- **Action**: 
  1. Auto-queue for next LAMMPS batch (within 24 hours)
  2. Send email to Safety Analysts
  3. Track in weekly report
- **SLA**: Verification complete within 72 hours

**P2 (Medium Priority) - Weekly Batch**:
- **Trigger**: Non-core systems (U-Pu-Zr, UN, UC, SiC)
- **Action**: 
  1. Accumulate until 10+ gaps or Monday 8am
  2. Process via LAMMPS batch runner
  3. Notify researchers of new verifications
- **SLA**: Verification complete within 1 week

**P3 (Low Priority) - Biweekly Batch**:
- **Trigger**: Extended properties across all systems
- **Action**: 
  1. Accumulate until 20+ gaps or 1st/3rd Monday
  2. Process via LAMMPS batch runner
  3. Include in biweekly research digest
- **SLA**: Verification complete within 2 weeks

**P4 (Informational) - Monthly Report**:
- **Trigger**: Missing uncertainty, incomplete source tags
- **Action**: 
  1. Log to data quality dashboard
  2. Include in monthly Data Quality Report
  3. Flag for domain expert review during quarterly audit
- **SLA**: Address within quarterly review cycle

### 2.3 Human-in-the-Loop Gates

**Gate 1: Reference Value Entry** (Manual Only)
- **Trigger**: Gap with no reference value in database
- **Workflow**:
  1. System flags gap: "No reference found"
  2. Email sent to Domain Experts: "Reference needed for {system} {property}"
  3. Expert searches literature (NIST IPR, OpenKIM, Materials Project)
  4. Expert enters reference via admin form
  5. System validates: uncertainty required for P0
  6. Reference enters staging table (status: PENDING)
  7. Peer review required for P0 systems
  8. Approved reference → Auto-triggers verification

**Gate 2: F-Grade Adjudication** (Semi-Automated)
- **Trigger**: LAMMPS verification returns F grade (>20% deviation)
- **Workflow**:
  1. System auto-flags: "F-grade - human review required"
  2. Email sent to Domain Experts + Potential Owner
  3. Expert investigates:
     - Reference value incorrect?
     - Potential file corrupted?
     - LAMMPS template error?
     - Property genuinely not calculable?
  4. Expert selects resolution:
     - Update reference value → Re-queue verification
     - Flag potential as "not_applicable" for property
     - Request LAMMPS template review
     - Accept F-grade (document rationale)

**Gate 3: Reference Value Conflicts** (Manual)
- **Trigger**: Multiple reference values exist for same (system, property)
- **Workflow**:
  1. System detects: `COUNT(DISTINCT value) > 1`
  2. Email sent to Domain Experts: "Conflict detected for {system} {property}"
  3. Expert reviews sources (DOIs, uncertainty, experimental vs DFT)
  4. Expert selects primary value + documents rationale
  5. Secondary values marked as "superseded"
  6. Audit trail records conflict resolution

**Gate 4: Non-EAM Potential Template Selection** (Manual)
- **Trigger**: Potential with Buckingham, Tersoff, AIREBO pair_style
- **Workflow**:
  1. System detects non-EAM pair_style from file header
  2. Email sent to LAMMPS Template Team: "Template needed for {pair_style}"
  3. Team member creates custom LAMMPS input template
  4. Template tested with sample potential
  5. Approved template stored in template library
  6. Potential queued for verification with new template

**Gate 5: New System First Verification** (Semi-Automated)
- **Trigger**: First-time verification for (element_system, property) tuple
- **Workflow**:
  1. System flags: "First verification for {system} {property}"
  2. Verification proceeds as normal
  3. Email sent to Domain Experts: "First result ready for review"
  4. Expert sanity-checks result:
     - Value physically reasonable?
     - Deviation within expected range?
     - No LAMMPS errors in log?
  5. Expert approves → verification marked as "verified"
  6. Future verifications for same tuple proceed automatically

---

## Part 3: Stakeholder Notification System

### 3.1 Notification Matrix by Role and Priority

**Roles**:
1. **Safety Analysts** (P0 consumers)
2. **Researchers** (P1-P3 consumers)
3. **Database Maintainers** (P4 consumers)
4. **Domain Experts** (Reference approvers)
5. **LAMMPS Template Team** (Non-EAM support)
6. **CPO/CTO** (Executive visibility)

**Notification Channels**:
- **Email**: All roles (default)
- **SMS**: P0 only (Safety Analysts)
- **Slack**: All roles (optional per-user preference)
- **Dashboard**: All roles (passive notifications)

**Notification Types**:

| Event | P0 | P1 | P2 | P3 | P4 | Channel | Template |
|-------|----|----|----|----|-------|---------|----------|
| New gap detected | ✅ Immediate | ✅ Same-day | ⏳ Weekly | ⏳ Biweekly | 📊 Monthly | Email/SMS | `new_gap_P0.txt` |
| Verification complete | ✅ Immediate | ✅ Same-day | ✅ Weekly | ✅ Biweekly | 📊 Monthly | Email | `verification_complete.txt` |
| F-grade flag | ✅ Immediate | ✅ Same-day | ✅ Weekly | ❌ Skip | ❌ Skip | Email | `f_grade_review.txt` |
| Reference conflict | ✅ Immediate | ✅ Immediate | ✅ Immediate | ❌ Skip | ❌ Skip | Email | `conflict_detected.txt` |
| P0 SLA breach | ✅ Immediate + Escalation | ❌ Skip | ❌ Skip | ❌ Skip | ❌ Skip | Email/SMS | `sla_breach.txt` |
| Weekly gap summary | 📊 Include | 📊 Include | 📊 Include | 📊 Include | 📊 Include | Email | `weekly_summary.html` |
| Monthly quality report | 📊 Include | 📊 Include | 📊 Include | 📊 Include | 📊 Include | Email | `monthly_quality.html` |

**Notification Frequency Controls**:

To prevent alert fatigue, users can customize:
- **Digest mode**: Receive daily/weekly digest instead of immediate alerts
- **Filter by system**: Only notify for U, UO₂, Zr (my systems)
- **Filter by property**: Only notify for lattice_constant, elastic_constants
- **Quiet hours**: No alerts 6pm-8am PT (except P0 SLA breaches)

**Unsubscribe Rules**:
- P0 notifications: **Cannot unsubscribe** (safety-critical)
- P1-P3 notifications: Can unsubscribe, must acknowledge risk
- P4 notifications: Can unsubscribe (informational only)

### 3.2 Notification Templates

**Template 1: New P0 Gap Detected (Email + SMS)**

```
SUBJECT: 🔴 URGENT: P0 Verification Gap Detected - U lattice_constant

EMAIL BODY:
────────────────────────────────────────────────────────────────────────────
A new P0 safety-critical verification gap has been detected:

GAP DETAILS:
  System: U (BCC)
  Property: lattice_constant
  Potential: EAM_U_UO3_Zr (potential_id: abc-123)
  Gap Type: unverified
  Detected: 2026-06-13 06:00:00 PT

IMPACT:
  This property is required for reactor safety analysis.
  Current status: No verification exists for this potential.

AUTOMATED ACTION:
  ✅ Queued for LAMMPS verification (batch ID: xyz-789)
  ⏱️  Expected completion: 2026-06-13 10:00:00 PT

YOUR ACTION REQUIRED:
  1. Review potential details: /admin/verification/gaps/{gap_id}
  2. Confirm reference value is correct: 3.524 Å ±0.002
  3. If reference is incorrect, update before 10:00 AM PT

SLA: Verification must complete within 24 hours (2026-06-14 06:00:00 PT)

VIEW ALL P0 GAPS: /admin/verification/gaps?priority=P0
────────────────────────────────────────────────────────────────────────────

SMS BODY:
P0 gap detected: U lattice_constant. Auto-queued for verification. 
Review by 10am PT: https://nucpot.org/admin/verification/gaps/{gap_id}
```

**Template 2: F-Grade Review Request (Email)**

```
SUBJECT: ⚠️ F-Grade Adjudication Required - UO₂ elastic_constants

EMAIL BODY:
────────────────────────────────────────────────────────────────────────────
LAMMPS verification returned F-grade (>20% deviation) for:

VERIFICATION DETAILS:
  Potential: EAM_U_UO3_Zr (potential_id: abc-123)
  System: UO₂ (fluorite)
  Property: elastic_constants (C11, C12, C44)
  Reference: 393 GPa, 145 GPa, 68 GPa (DOI: 10.1234/example)
  LAMMPS result: 298 GPa, 112 GPa, 52 GPa
  Deviation: -24%, -23%, -24%
  Grade: F

INVESTIGATION REQUIRED:
  Possible causes:
  1. Reference value incorrect for this potential type
  2. Potential file corrupted or incompatible pair_style
  3. LAMMPS template error for this property calculation
  4. Property genuinely not calculable with this potential

YOUR ACTION REQUIRED:
  1. Review potential file: /admin/potentials/{potential_id}
  2. Review LAMMPS log: /admin/verification/logs/{verification_id}
  3. Select resolution:
     □ Update reference value → [Re-queue verification]
     □ Flag potential as "not_applicable" for this property
     □ Request LAMMPS template review
     □ Accept F-grade (document rationale)

DEADLINE: Please adjudicate within 72 hours (2026-06-16 06:00:00 PT)
────────────────────────────────────────────────────────────────────────────
```

**Template 3: Weekly Gap Summary (HTML Email)**

```
SUBJECT: 📊 Weekly Verification Gap Summary - Week of June 12, 2026

HTML BODY:
────────────────────────────────────────────────────────────────────────────
<h2>Verification Gap Summary</h2>
<p>Reporting period: June 12, 2026 - June 13, 2026</p>

<h3>Executive Dashboard</h3>
<table>
  <tr><th>Metric</th><th>Value</th><th>Change</th></tr>
  <tr><td>Total Potentials</td><td>65</td><td>-</td></tr>
  <tr><td>Verified Count</td><td>1,234</td><td class="positive">+45 (+3.8%)</td></tr>
  <tr><td>Unverified Count</td><td>567</td><td class="negative">+12 (+2.2%)</td></tr>
  <tr><td>Coverage %</td><td>68.5%</td><td class="positive">+1.2%</td></tr>
</table>

<h3>Gap Breakdown by Priority</h3>
<table>
  <tr><th>Priority</th><th>New Gaps</th><th>Resolved</th><th>Net Change</th><th>Total</th></tr>
  <tr class="p0"><td>P0 (Critical)</td><td>3</td><td>5</td><td class="positive">-2</td><td>12</td></tr>
  <tr class="p1"><td>P1 (High)</td><td>8</td><td>12</td><td class="positive">-4</td><td>45</td></tr>
  <tr class="p2"><td>P2 (Medium)</td><td>15</td><td>10</td><td class="negative">+5</td><td>123</td></tr>
  <tr class="p3"><td>P3 (Low)</td><td>22</td><td>18</td><td class="negative">+4</td><td>234</td></tr>
  <tr class="p4"><td>P4 (Quality)</td><td>45</td><td>30</td><td class="negative">+15</td><td>567</td></tr>
</table>

<h3>P0 Safety-Critical Gaps Requiring Attention</h3>
<ul>
  <li>U (BCC) - vacancy_formation_energy (2 gaps) → [View Details]</li>
  <li>UO₂ (fluorite) - elastic_constants (1 gap) → [View Details]</li>
  <li>Zr (HCP) - lattice_constant (1 gap) → [View Details]</li>
</ul>

<h3>Verification Completions This Week</h3>
<ul>
  <li>45 verifications completed (32 A-grade, 10 B-grade, 3 C-grade)</li>
  <li>Top performer: EAM_U_UO3_Zr potential (8 properties verified)</li>
</ul>

<h3>Actions Required</h3>
<ul>
  <li><strong>Safety Analysts</strong>: Review 3 new P0 gaps</li>
  <li><strong>Domain Experts</strong>: Adjudicate 2 F-grade reviews</li>
  <li><strong>Database Team</strong>: 15 P4 quality gaps need reference updates</li>
</ul>

<p><a href="/admin/verification/gaps">View Full Gap Dashboard</a></p>
<p><a href="/api/v1/gaps/report?format=csv&week=2026-06-12">Download CSV Report</a></p>
────────────────────────────────────────────────────────────────────────────
```

### 3.3 Notification Delivery System

**Implementation**: Celery + SendGrid

**Queue Priority**:
- `queue.high`: P0 notifications (immediate delivery)
- `queue.normal`: P1-P3 notifications (within 1 hour)
- `queue.low`: P4 notifications (within 4 hours)

**Retry Logic**:
- **Retry 1**: 5 minutes later (transient failure)
- **Retry 2**: 30 minutes later (SMTP timeout)
- **Retry 3**: 2 hours later (rate limit)
- **Final**: Failed notifications logged to `/admin/notifications/failed`

**Bounce Handling**:
- Hard bounces (email invalid) → Mark user email as invalid, disable notifications
- Soft bounces (mailbox full) → Retry 6 hours later
- Unsubscribe requests → Honor within 24 hours

---

## Part 4: Regular Reporting Cadence and Format

### 4.1 Reporting Schedule

**Daily Reports** (automated, email only):
- **Time**: 7am PT
- **Recipients**: Safety Analysts (P0 only)
- **Content**: Yesterday's P0 gaps + verifications
- **Format**: Plain text email
- **Archive**: 30 days

**Weekly Reports** (automated, HTML email + dashboard):
- **Time**: Monday 8am PT
- **Recipients**: All stakeholders
- **Content**: Gap breakdown, verification summary, action items
- **Format**: HTML email + CSV download link
- **Archive**: 2 years

**Monthly Quality Reports** (automated, HTML email + PDF):
- **Time**: 1st of month 9am PT
- **Recipients**: CPO, CTO, Database Maintainers
- **Content**: 
  - Coverage trend analysis (6-month moving average)
  - Grade distribution (A-F percentages)
  - Uncertainty coverage percentage
  - Source DOI completeness
  - P0 SLA compliance rate
- **Format**: HTML email + PDF attachment + CSV download
- **Archive**: 7 years

**Quarterly Audit Reports** (manual, PDF):
- **Time**: 1st week of quarter
- **Recipients**: Board, Domain Experts
- **Content**:
  - P0 reference value audit (expert review)
  - Verification trend analysis (quarter-over-quarter)
  - Data quality scorecard
  - Recommendations for improvements
- **Format**: PDF (executive summary + detailed appendix)
- **Archive**: Permanent (document retention)

**Annual Strategic Reports** (manual, PDF + presentation):
- **Time**: Q4 board meeting
- **Recipients**: Board, stakeholders
- **Content**:
  - Year-over-year verification coverage growth
  - ROI analysis of verification automation
  - Alignment with OpenKIM / Materials Project
  - Strategic roadmap for next year
- **Format**: PDF + PowerPoint slides
- **Archive**: Permanent

### 4.2 Report Templates

**Template: Daily P0 Brief (Plain Text)**

```
NucPot Verification Daily Brief - June 13, 2026
═════════════════════════════════════════════════════════════════════════════

NEW P0 GAPS (Yesterday)
────────────────────────────────────────────────────────────────────────────
1. U (BCC) - lattice_constant - EAM_U_UO3_Zr
   Detected: 2026-06-12 14:32 PT
   Action: Queued for verification (batch ID: xyz-789)
   ETA: 2026-06-13 10:00 PT
   Link: https://nucpot.org/admin/verification/gaps/{gap_id}

2. UO₂ (fluorite) - elastic_constants - EAM_U_UO3_Zr
   Detected: 2026-06-12 14:32 PT
   Action: Queued for verification (batch ID: xyz-789)
   ETA: 2026-06-13 10:00 PT
   Link: https://nucpot.org/admin/verification/gaps/{gap_id}

VERIFICATIONS COMPLETED (Yesterday)
────────────────────────────────────────────────────────────────────────────
1. Zr (HCP) - vacancy_formation_energy - EAM_Zr
   Result: A-grade (1.2% deviation)
   Completed: 2026-06-12 16:45 PT
   Link: https://nucpot.org/admin/verification/{verification_id}

2. Fe (BCC) - elastic_constants - EAM_Fe
   Result: B-grade (4.5% deviation)
   Completed: 2026-06-12 16:47 PT
   Link: https://nucpot.org/admin/verification/{verification_id}

P0 SLA STATUS
────────────────────────────────────────────────────────────────────────────
Current P0 gaps: 12
Within SLA: 11 (92%)
SLA breaches: 1 (U lattice_constant - overdue 2 hours)
Action: Escalated to CPO

VIEW FULL DASHBOARD: https://nucpot.org/admin/verification/gaps?priority=P0
═════════════════════════════════════════════════════════════════════════════
```

**Template: Monthly Quality Report (HTML)**

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    .metric-card { background: #f5f5f5; padding: 20px; margin: 10px 0; border-radius: 8px; }
    .positive { color: green; }
    .negative { color: red; }
    .neutral { color: gray; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background-color: #4CAF50; color: white; }
  </style>
</head>
<body>
  <h2>NucPot Verification Quality Report - June 2026</h2>
  <p>Reporting period: June 1, 2026 - June 30, 2026</p>

  <h3>Executive Summary</h3>
  <div class="metric-card">
    <h4>Overall Coverage: <strong class="positive">72.3%</strong> <span class="positive">(+3.8% from May)</span></h4>
    <p>Total verified tuples: 1,456 / 2,014</p>
  </div>

  <h3>Coverage Trends (6-Month Moving Average)</h3>
  <table>
    <tr>
      <th>Month</th>
      <th>Coverage %</th>
      <th>Change</th>
      <th>P0 Gaps</th>
      <th>P0 SLA %</th>
    </tr>
    <tr><td>January</td><td>58.2%</td><td>-</td><td>28</td><td>85%</td></tr>
    <tr><td>February</td><td>61.5%</td><td class="positive">+3.3%</td><td>24</td><td>88%</td></tr>
    <tr><td>March</td><td>64.8%</td><td class="positive">+3.3%</td><td>20</td><td>90%</td></tr>
    <tr><td>April</td><td>67.2%</td><td class="positive">+2.4%</td><td>18</td><td>92%</td></tr>
    <tr><td>May</td><td>68.5%</td><td class="positive">+1.3%</td><td>15</td><td>94%</td></tr>
    <tr><td>June</td><td>72.3%</td><td class="positive">+3.8%</td><td><strong>12</strong></td><td><strong>96%</strong></td></tr>
  </table>

  <h3>Grade Distribution</h3>
  <table>
    <tr>
      <th>Grade</th>
      <th>Count</th>
      <th>Percentage</th>
      <th>Threshold</th>
    </tr>
    <tr><td>A</td><td>892</td><td class="positive">61.3%</td><td>≤2% deviation</td></tr>
    <tr><td>B</td><td>312</td><td>21.4%</td><td>≤5% deviation</td></tr>
    <tr><td>C</td><td>156</td><td>10.7%</td><td>≤10% deviation</td></tr>
    <tr><td>D</td><td>56</td><td>3.8%</td><td>≤20% deviation</td></tr>
    <tr><td>F</td><td>40</td><td class="negative">2.8%</td><td>>20% deviation</td></tr>
  </table>

  <h3>Data Quality Metrics</h3>
  <table>
    <tr>
      <th>Metric</th>
      <th>Current</th>
      <th>Target</th>
      <th>Status</th>
    </tr>
    <tr><td>Uncertainty Coverage</td><td class="positive">78.5%</td><td>100% (P0)</td><td class="positive">On Track</td></tr>
    <tr><td>Source DOI Coverage</td><td class="positive">92.3%</td><td>100%</td><td class="positive">On Track</td></tr>
    <tr><td>Confidence: HIGH</td><td class="positive">45.2%</td><td>≥40%</td><td class="positive">Target Met</td></tr>
    <tr><td>Confidence: MEDIUM</td><td class="neutral">38.7%</td><td>-</td><td>-</td></tr>
    <tr><td>Confidence: LOW</td><td class="negative">16.1%</td><td>≤20%</td><td class="positive">Within Range</td></tr>
    <tr><td>Value Conflicts</td><td class="positive">8</td><td>≤10</td><td class="positive">Within Limit</td></tr>
  </table>

  <h3>P0 SLA Compliance</h3>
  <div class="metric-card">
    <h4>June P0 SLA Performance: <strong class="positive">96%</strong></h4>
    <p>Total P0 gaps: 48</p>
    <p>Resolved within 24h: 46</p>
    <p>SLA breaches: 2 (both due to reference value conflicts)</p>
  </div>

  <h3>Action Items for July</h3>
  <ul>
    <li><strong>Critical</strong>: Resolve 8 P0 gaps (target: zero by July 15)</li>
    <li><strong>High</strong>: Improve uncertainty coverage for P0 properties (target: 100%)</li>
    <li><strong>Medium</strong>: Address 2 reference value conflicts blocking P0 verification</li>
    <li><strong>Low</strong>: Quarterly audit of P0 reference values (scheduled July 8)</li>
  </ul>

  <p><a href="/admin/verification/coverage-matrix">View Coverage Matrix</a></p>
  <p><a href="/api/v1/gaps/report?format=csv&month=2026-06">Download CSV</a></p>
</body>
</html>
```

### 4.3 Custom Report Builder

**Endpoint**: `GET /admin/verification/reports/builder`

**UI Features**:
- **Filter Builder**: Drag-and-drop interface for filters
  - By priority (checkbox: P0, P1, P2, P3, P4)
  - By system (multi-select dropdown)
  - By property (multi-select dropdown)
  - By date range (date picker)
  - By grade (checkbox: A, B, C, D, F)
- **Chart Selection**:
  - Coverage trend line chart
  - Grade distribution pie chart
  - System × property heatmap
  - Priority breakdown bar chart
- **Export Options**:
  - PDF (report with charts)
  - CSV (raw data)
  - XLSX (formatted spreadsheet with charts)
  - JSON (API response)
- **Schedule**:
  - One-time (immediate generation)
  - Recurring (daily/weekly/monthly)
  - Custom cron schedule

**Saved Reports**:
- Users can save custom report configurations
- Share saved reports with other users (by email)
- Subscribe to saved reports (auto-delivery via email)

---

## Part 5: Maintenance Workflow for Ongoing Quality

### 5.1 Continuous Quality Monitoring

**Automated Quality Checks** (run hourly):

```python
class QualityMonitor:
    """Continuous quality monitoring for verification data."""

    async def check_uncertainty_coverage(
        self,
        priority_filter: Priority = Priority.P0,
    ) -> QualityReport:
        """Check % of reference values with uncertainty estimates."""
        # Query: % WITH uncertainty IS NOT NULL WHERE priority = P0
        # Target: 100%
        # Alert if: < 95% for P0, < 80% for P1-P3
        ...

    async def check_source_doi_coverage(
        self,
        promoted_only: bool = True,
    ) -> QualityReport:
        """Check % of reference values with source DOI."""
        # Query: % WITH source_doi IS NOT NULL WHERE status = 'promoted'
        # Target: 100%
        # Alert if: < 90%
        ...

    async def check_value_conflicts(
        self,
        system: str | None = None,
    ) -> QualityReport:
        """Detect conflicting reference values for same (system, property)."""
        # Query: COUNT(DISTINCT value) > 1 per (element, phase, property)
        # Alert if: Any conflicts found
        ...

    async def check_p0_sla_compliance(
        self,
        window_hours: int = 24,
    ) -> QualityReport:
        """Check % of P0 gaps resolved within SLA."""
        # Query: % verified WHERE priority = P0 AND verified_at - created_at < 24h
        # Target: ≥95%
        # Alert if: < 90%
        ...
```

**Alert Thresholds**:

| Metric | Target | Warning | Critical | Alert Recipients |
|--------|--------|---------|----------|------------------|
| P0 uncertainty coverage | 100% | < 95% | < 90% | Database Maintainers, CPO |
| P0 SLA compliance | ≥95% | < 95% | < 90% | CTO, Safety Analysts |
| Source DOI coverage | 100% | < 95% | < 90% | Database Maintainers |
| Value conflicts | 0 | 1-5 | > 5 | Domain Experts, CPO |
| Grade distribution (F) | < 5% | > 5% | > 10% | Domain Experts, CTO |

### 5.2 Quarterly Audit Workflow

**Cadence**: Q1 (January), Q2 (April), Q3 (July), Q4 (October)

**Participants**: 
- Domain Experts (lead)
- Safety Analysts (review)
- Database Maintainers (support)

**Audit Checklist**:

```markdown
## Quarterly Verification Quality Audit - Q2 2026

### Part 1: P0 Reference Value Audit
For each P0 system (U, UO₂, Zr, U-Zr, Fe):

- [ ] **Reference source verification**
  - [ ] All reference values have primary literature citations
  - [ ] DOIs are valid and resolvable
  - [ ] Source type is clearly documented (experimental/DFT/review)

- [ ] **Uncertainty estimate review**
  - [ ] All P0 properties have uncertainty estimates
  - [ ] Uncertainty values are reasonable (e.g., ±2% for lattice constants)
  - [ ] Confidence levels are appropriate (HIGH for experimental, MEDIUM for DFT)

- [ ] **Value conflict check**
  - [ ] No conflicting values exist for same (system, property)
  - [ ] If conflicts exist, primary value is documented and justified

- [ ] **Verification history review**
  - [ ] At least 3 independent verifications exist for each P0 property
  - [ ] Grade distribution is reasonable (≥50% A-grade, ≤10% F-grade)
  - [ ] F-grade verifications have adjudication notes

### Part 2: P1-P3 System Audit
Sample of 10 P1-P3 systems:

- [ ] **Reference completeness**
  - [ ] Core properties have reference values
  - [ ] Source DOI coverage ≥80%
  - [ ] Uncertainty estimates present for ≥50%

- [ ] **Verification coverage**
  - [ ] At least 1 verification exists per property
  - [ ] Recent verifications (within last 6 months)

### Part 3: Data Quality Scorecard
- [ ] Overall coverage %: ___
- [ ] Uncertainty coverage %: ___
- [ ] Source DOI coverage %: ___
- [ ] Value conflict count: ___
- [ ] P0 SLA compliance %: ___
- [ ] Grade A-F distribution: A:___% B:___% C:___% D:___% F:___%

### Part 4: Recommendations
1. ___
2. ___
3. ___

### Part 5: Action Items for Next Quarter
- [ ] ___ (Owner: ___, Due: ___)
- [ ] ___ (Owner: ___, Due: ___)
- [ ] ___ (Owner: ___, Due: ___)
```

**Audit Deliverables**:
1. **Audit Report** (PDF) - Executive summary + detailed findings
2. **Action Plan** (Jira tickets) - Track remediation items
3. **Scorecard Update** (Dashboard) - Refresh quality metrics
4. **Board Briefing** (Slides) - Q3 board meeting presentation

### 5.3 Annual Strategic Review

**Cadence**: Q4 (November/December)

**Participants**: 
- Board (review and approval)
- CPO (presentation)
- CTO (technical assessment)
- Domain Experts (domain perspective)

**Review Topics**:

1. **Year-Over-Year Coverage Growth**
   - Verification coverage % (Dec 2025 vs Dec 2026)
   - P0 gap trend (target: zero)
   - New systems added (UN, UC, SiC coverage)

2. **Automation ROI Analysis**
   - Time saved via batch verification (hours saved)
   - SLA improvement (pre-automation vs post-automation)
   - Cost per verification (manual vs automated)

3. **External Alignment**
   - OpenKIM integration status
   - Materials Project data exchange
   - Publication opportunities (verification database paper)

4. **Risk Assessment**
   - Outstanding P0 gaps
   - Data quality issues
   - Technical debt (non-EAM potential support, etc.)

5. **Next Year Roadmap**
   - Coverage targets (e.g., 85% by Dec 2027)
   - Automation priorities (e.g., reference auto-fill)
   - Resource requirements (FTE, budget)

**Strategic Deliverables**:
1. **Annual Report** (PDF) - Comprehensive review
2. **Roadmap Presentation** (PowerPoint) - Board meeting
3. **Budget Request** - FTE and infrastructure needs
4. **Publication Draft** - Paper for peer-reviewed journal

### 5.4 Continuous Improvement Feedback Loop

**Feedback Sources**:

1. **User Feedback** (quarterly survey):
   - Safety Analysts: "Are P0 alerts useful? Too many/few?"
   - Researchers: "Is coverage matrix easy to interpret?"
   - Domain Experts: "Is adjudication workflow efficient?"

2. **System Metrics** (monthly review):
   - Notification unsubscribe rate (alert fatigue?)
   - Report download count (which reports are most useful?)
   - Dashboard usage (which views are most popular?)

3. **Process Metrics** (quarterly review):
   - SLA breach rate (are targets realistic?)
   - Average time from gap detection to resolution
   - F-grade adjudication time (too long?)

**Improvement Cycle**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ COLLECT FEEDBACK                                                        │
│ Quarterly survey + monthly metrics + quarterly process review           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ ANALYZE + PRIORITIZE                                                    │
│ CPO + CTO review feedback → Identify top 3 improvement opportunities    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ DESIGN SOLUTIONS                                                        │
│ Workflow Designer proposes process changes                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ IMPLEMENT + TEST                                                        │
│ Pilot with small user group → Measure impact                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ DEPLOY + MONITOR                                                        │
│ Roll out to all users → Track metrics for 2 quarters                    │
└─────────────────────────────────────────────────────────────────────────┘
```

**Improvement Backlog** (tracked in Jira):
- Example: "Reduce P0 false positives by tuning gap detection queries"
- Example: "Add SMS opt-in for Safety Analysts"
- Example: "Improve report export performance for large datasets"

---

## Part 6: Implementation Roadmap

### 6.1 Phase Breakdown

**Phase 1: Foundation (Week 1-2)**
- Deliverables:
  - `verification_coverage` table creation
  - Gap detection service (Query 1-3)
  - Daily gap scan Celery task
  - P0 notification templates
- Owner: CTO Team
- Dependencies: NFM-85 approved

**Phase 2: Core Workflows (Week 3-4)**
- Deliverables:
  - Priority-based routing logic (P0-P4)
  - Notification delivery system (Celery + SendGrid)
  - Weekly summary report template
  - P0 SLA monitoring
- Owner: CTO Team
- Dependencies: Phase 1 complete

**Phase 3: Human-in-the-Loop Gates (Week 5-6)**
- Deliverables:
  - Reference value entry workflow
  - F-grade adjudication workflow
  - Reference conflict resolution UI
  - Quarterly audit checklist
- Owner: CPO Team + CTO Team
- Dependencies: Phase 2 complete

**Phase 4: Reporting & Visualization (Week 7-8)**
- Deliverables:
  - Coverage matrix heatmap (NFM-92)
  - Monthly quality report template
  - Custom report builder UI
  - API endpoints for all reports
- Owner: CTO Team
- Dependencies: Phase 3 complete

**Phase 5: Continuous Improvement (Week 9-10)**
- Deliverables:
  - Quality monitoring service (hourly checks)
  - Quarterly audit workflow (first audit: Q3 2026)
  - User feedback survey (launch)
  - Improvement backlog setup
- Owner: CPO Team
- Dependencies: Phase 4 complete

### 6.2 Risk Register

| Risk | Impact | Probability | Mitigation | Owner |
|------|--------|-------------|------------|-------|
| **Alert fatigue** - Too many P0 notifications | High | Medium | Tune gap detection queries to reduce false positives; implement digest mode | CTO |
| **SLA breaches** - Cannot meet 24h P0 target | High | Low | Build buffer in queue capacity; escalation procedure for breaches | CPO |
| **Domain expert bottleneck** - Reference entry takes too long | Medium | High | Prioritize P0 only for now; hire data expert (NFM-83.4) | CPO |
| **Notification delivery failures** - Email/SMS system down | Medium | Low | Retry logic; failed notification queue; secondary Slack channel | CTO |
| **Quality audit not done** - Quarterly audit skipped | Medium | Medium | Calendar invite for audit; board oversight; link to board meeting | CPO |

### 6.3 Success Metrics

**Week 1-2 (Foundation)**:
- [ ] Gap detection scan runs daily at 6am PT
- [ ] `verification_coverage` table populated with existing data
- [ ] P0 notification template tested with Safety Analysts

**Week 3-4 (Core Workflows)**:
- [ ] P0 gaps auto-queued within 4 hours of detection
- [ ] Weekly summary report sent every Monday 8am PT
- [ ] P0 SLA compliance ≥90%

**Week 5-6 (Human Gates)**:
- [ ] First reference value entered via new workflow
- [ ] First F-grade adjudication completed
- [ ] Reference conflict resolution workflow tested

**Week 7-8 (Reporting)**:
- [ ] Coverage matrix heatmap deployed to /admin/verification/coverage-matrix
- [ ] Monthly quality report sent July 1, 2026
- [ ] Custom report builder UI functional

**Week 9-10 (Continuous Improvement)**:
- [ ] Quality monitoring checks run hourly
- [ ] First quarterly audit scheduled for Q3 2026 (week of July 8)
- [ ] User feedback survey launched

**6-Month Review (December 2026)**:
- [ ] Overall verification coverage ≥80%
- [ ] P0 gaps ≤5
- [ ] P0 SLA compliance ≥95%
- [ ] User satisfaction score ≥4/5

---

## Part 7: Conclusion and Next Steps

### 7.1 Summary

This workflow design translates the NFM-84 domain requirements and NFM-85 technical architecture into **operational processes** that:

1. **Automate high-ROI activities**: Gap detection, classification, routing, reporting
2. **Preserve human judgment for critical decisions**: Reference entry, conflict resolution, F-grade adjudication
3. **Prevent alert fatigue**: Priority-based notification matrix with unsubscribe controls
4. **Ensure sustainable quality**: Continuous monitoring, quarterly audits, annual reviews
5. **Scale gracefully**: Batch processing for P2-P3, immediate action for P0

### 7.2 Board Action Required

**Request**: Approve workflow design and authorize implementation

**Approval Items**:
1. ✅ Workflow design document (this document)
2. ✅ Implementation roadmap (8 weeks, Phases 1-5)
3. ✅ Resource allocation (CTO Team: 6 weeks, CPO Team: 4 weeks)
4. ✅ Budget for notification system (SendGrid: ~$50/month)
5. ✅ Hiring trigger for Data Expert (NFM-83.4) - referenced as blocked

**Next Steps After Approval**:
1. CPO creates child issues for Phases 1-5
2. CTO assigns NFM-90 (gap detection service)
3. CPO assigns Q3 2026 quarterly audit to Domain Experts
4. Workflow Designer unblocks NFM-83.4 (Data Expert hiring decision)

### 7.3 Handoff to Implementation

**Documentation for Implementers**:
- NFM-85 (Technical Architecture) - Database schema, API design, workflow state machine
- This document (NFM-86) - Operational workflows, notification templates, reporting cadence
- NFM-84 Follow-up Issues - Detailed task breakdown (NFM-90 through NFM-95)

**Questions for Implementation Team**:
1. Who will be the primary P0 notification recipient (Safety Analyst lead)?
2. What is the acceptable P0 SLA (24 hours? 48 hours? 72 hours)?
3. Should we implement SMS notifications for P0 or email only?
4. Who will own the quarterly audit process (Domain Expert lead)?
5. Should the first quarterly audit be Q3 2026 or Q4 2026?

---

*Document Status: DRAFT - Ready for Board Review*
*Next Action: Present to CEO for NFM-86 disposition*
*Dependencies Unblocked: NFM-83.4 can proceed after board approval*
