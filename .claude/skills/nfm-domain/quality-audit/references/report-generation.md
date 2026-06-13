# Audit Report Generation

## Report Structure

### Standard Audit Report

```markdown
# NFM Database P0 Quality Audit Report
**Period**: [Q1/Q2/Q3/Q4] [YEAR]
**Audit Date**: [DATE]
**Auditor**: Nuclear Domain Expert Agent
**Report ID**: AUDIT-[YEAR]-Q[1-4]-[HASH]

---

## Executive Summary

**Overall Compliance**: [XX.X]%
**Critical Issues**: [COUNT]
**Systems Audited**: 5/5
**Overall Audit Status**: [PASS / FAIL / USE WITH CAUTION]
**Overall Confidence**: [0-100]%

### Key Findings
- [Finding 1]
- [Finding 2]
- [Finding 3]

### Action Required
- [ ] [Immediate action items]
- [ ] [This quarter]
- [ ] [Next quarter]

---

## System-by-System Results

### 1. Uranium (U) — [PASS/FAIL] — [XX.X]% Compliance

| Check | Status | Details |
|-------|--------|---------|
| Uncertainty Coverage | [100%/XX%] | [N of M values documented] |
| Source Credibility | [100%/XX%] | [Tier breakdown] |
| Verification Recency | [100%/XX%] | [Oldest: DATE, Newest: DATE] |
| Conflict Resolution | [100%/XX%] | [Open: N, Resolved: M] |
| Source Traceability | [100%/XX%] | [With DOI: N, Without: M] |

**Issues**: [List or "None identified"]

### 2. Uranium Dioxide (UO₂) — [PASS/FAIL] — [XX.X]% Compliance

[Same table structure]

### 3. Zirconium (Zr) — [PASS/FAIL] — [XX.X]% Compliance

[Same table structure]

### 4. Iron (Fe) — [PASS/FAIL] — [XX.X]% Compliance

[Same table structure]

### 5. U-Zr Alloys — [PASS/FAIL] — [XX.X]% Compliance

[Same table structure]

---

## Anomaly Summary

| Type | Severity | Count | % of Total |
|------|----------|-------|------------|
| A — Missing Uncertainty | CRITICAL | [N] | [X.X]% |
| B — Low Credibility | HIGH | [N] | [X.X]% |
| C — Outdated Verification | MEDIUM | [N] | [X.X]% |
| D — Value Out of Range | MED-HIGH | [N] | [X.X]% |
| E — Inconsistent Uncertainty | LOW-MED | [N] | [X.X]% |
| F — Confidence Drop | LOW | [N] | [X.X]% |
| **Total** | | **[N]** | **[X.X]%** |

---

## Detailed Anomalies

### CRITICAL (Immediate Action Required)

| ID | System | Property | Anomaly Type | Description | Recommended Action |
|----|--------|----------|--------------|-------------|-------------------|
| [ID] | [System] | [Property] | [Type] | [Description] | [Action] |

### HIGH (Action This Quarter)

[Same table]

### MEDIUM (Action Next Quarter)

[Same table]

### LOW (Monitor)

[Same table]

---

## Recommendations

### Priority 1 — Critical (Immediate)
1. [Action item with owner and deadline]

### Priority 2 — High (This Quarter)
1. [Action item with owner and deadline]

### Priority 3 — Medium (Next Quarter)
1. [Action item with owner and deadline]

---

## Trend Analysis

| System | Q-3 | Q-2 | Q-1 | Current | Trend |
|--------|-----|-----|-----|---------|-------|
| Uranium | [%] | [%] | [%] | [%] | [↑/→/↓] |
| UO₂ | [%] | [%] | [%] | [%] | [↑/→/↓] |
| Zr | [%] | [%] | [%] | [%] | [↑/→/↓] |
| Fe | [%] | [%] | [%] | [%] | [↑/→/↓] |
| U-Zr | [%] | [%] | [%] | [%] | [↑/→/↓] |

**Trend Assessment**: [Overall trend description]

---

## Confidence Score Calculation

```
Confidence = (Base Score × Coverage Factor × Recency Factor × Credibility Factor)

Base Score = (Compliant Items / Total Items) × 100
Coverage Factor = Uncertainty Coverage Rate (0.0-1.0)
Recency Factor = 1.0 - (Overdue Items / Total Items)
Credibility Factor = Average Source Tier / 4
```

**Current Confidence**: [XX.X]%

---

## Disposition

**Audit Status**: [PASS / FAIL / USE WITH CAUTION]
**Requires Human Review**: [YES / NO]
**Escalation Level**: [None / CTO Attention / Immediate Action]
**Next Audit**: [DATE] (Quarterly schedule)

---

## Appendix

### Audit Configuration
- P0 Systems: U, UO₂, Zr, Fe, U-Zr
- Reference values checked: [N]
- Audit methodology: NFM-87 P0 checklist v1.0

### Data Sources Used
- Literature-search credibility scoring
- Nuclear-materials-knowledge property ranges
- LAMMPS-debugger for simulation quality issues

### Report History
- Previous audit: [DATE] — [XX.X]% compliance
- Year-over-year change: [+/-XX.X]%
```

---

## Report Templates (Machine-Readable)

### JSON Audit Summary

```json
{
  "audit_id": "AUDIT-2026-Q2-abc123",
  "period": "2026-Q2",
  "date": "2026-06-30",
  "auditor": "nuclear-domain-expert",
  "overall_compliance": 97.4,
  "confidence": 92.5,
  "status": "PASS",
  "systems": {
    "uranium": {
      "compliance": 98.2,
      "total_items": 55,
      "passing_items": 54,
      "issues": []
    },
    "uo2": {
      "compliance": 96.8,
      "total_items": 62,
      "passing_items": 60,
      "issues": [
        {"id": "UO2-001", "type": "C", "severity": "MEDIUM"}
      ]
    }
  },
  "anomalies": {
    "critical": 0,
    "high": 1,
    "medium": 3,
    "low": 5,
    "total": 9,
    "details": []
  },
  "trends": {
    "uranium": {"q-3": 97.0, "q-2": 98.0, "q-1": 97.5, "current": 98.2, "direction": "up"},
    "uo2": {"q-3": 98.0, "q-2": 97.5, "q-1": 97.0, "current": 96.8, "direction": "down"}
  },
  "recommendations": {
    "critical": [],
    "high": ["Re-verify UO2 thermal conductivity sources"],
    "medium": ["Update Zr reference date stamps"],
    "low": ["Monitor U-Zr uncertainty distribution"]
  },
  "requires_human_review": false,
  "next_audit": "2026-09-30"
}
```

---

## Disposition Logic

### Autonomous Sign-Off Criteria

Audit can be autonomously signed off when ALL of:
- Overall compliance ≥98%
- No CRITICAL anomalies
- Confidence score ≥70%
- All HIGH anomalies have documented remediation plans
- No unresolved conflicts

### Escalation Criteria

Human review required when ANY of:
- Overall compliance <98%
- Any CRITICAL anomaly found
- Confidence score <70%
- Rapidly declining trend (≥5% drop in 2 consecutive quarters)
- Unresolved HIGH anomaly from previous quarter

### Final Audit Status

| Status | Criteria | Action |
|--------|----------|--------|
| **PASS** | All criteria met, ≥98% compliance, no critical issues | Data approved for use |
| **USE WITH CAUTION** | ≥95% compliance, no critical issues, some HIGH issues | Data usable with documented caveats |
| **FAIL** | <95% compliance OR any critical issue | Data blocked for safety use, remediation required |

---

## Cross-References

- See p0-checklist.md for the complete validation criteria
- See anomaly-detection.md for statistical detection methods
- See SKILL.md for the full audit workflow
