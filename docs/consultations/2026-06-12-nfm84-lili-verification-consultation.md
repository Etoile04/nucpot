# NFM-84: Domain Expert Consultation — Nucpot Verification Requirements

**Date:** 2026-06-12
**Consultant:** Lili (NFMD Coach)
**Prepared by:** CEO Agent
**Issue:** NFM-84
**Status:** 🔄 Awaiting Consultation

---

## Purpose

This consultation framework structures the conversation with Lili (NFMD Coach) to understand verification requirements for the nucpot (nuclear materials) database. The goal is to define what constitutes a "verification gap" and establish stakeholder requirements for verification data collection and reporting.

---

## Background

The NFM platform includes a verification pipeline (NFM-66) that identifies discrepancies between reference values (from literature) and actual measured values. However, we need domain expertise to understand:

1. Which verification data is currently missing from nucpot
2. What constitutes a verification gap
3. Current manual processes and pain points
4. Stakeholder requirements for quality and coverage
5. Expected reporting format and frequency

---

## Consultation Questions

### 1. Verification Data Landscape

**Q1.1:** What types of verification data currently exist in nucpot vs. what's missing?
- [ ] What properties have verified values?
- [ ] What properties lack verification?
- [ ] Which elements/systems have complete verification?
- [ ] Which elements/systems have partial or no verification?

**Q1.2:** Where does the verification data come from?
- [ ] Experimental measurements?
- [ ] Computational simulations?
- [ ] Literature sources?
- [ ] Expert judgment?

**Q1.3:** What is the quality of existing verification data?
- [ ] How reliable are the sources?
- [ ] What confidence levels exist?
- [ ] Are there uncertainty estimates?

---

### 2. Defining "Verification Gap"

**Q2.1:** What is the precise definition of a "verification gap"?

**Q2.2:** How should we categorize gaps by severity?
- [ ] Critical gaps (safety/reliability impact)
- [ ] Important gaps (performance impact)
- [ ] Nice-to-have gaps (completeness)

**Q2.3:** What are the priority areas for verification?
- [ ] Which properties/elements/systems matter most?
- [ ] What are the consequences of missing verification?

---

### 3. Current Manual Processes

**Q3.1:** How is verification currently done manually?

**Q3.2:** What are the pain points in the current process?
- [ ] Time-consuming tasks?
- [ ] Error-prone steps?
- [ ] Manual data entry?
- [ ] Cross-referencing difficulties?

**Q3.3:** What would be the biggest wins from automation?

**Q3.4:** What should NOT be automated (human judgment required)?

---

### 4. Stakeholder Requirements

**Q4.1:** Who are the stakeholders for verification data?
- [ ] Researchers
- [ ] Engineers
- [ ] Safety analysts
- [ ] Regulatory bodies
- [ ] Other (specify)

**Q4.2:** What are their quality requirements?
- [ ] Minimum confidence levels?
- [ ] Required data completeness?
- [ ] Uncertainty tolerances?

**Q4.3:** What are their coverage requirements?
- [ ] Which systems MUST be verified?
- [ ] Acceptable gap percentages?

**Q4.4:** How often do they need verification reports?
- [ ] Real-time?
- [ ] Daily?
- [ ] Weekly?
- [ ] Monthly?
- [ ] On-demand?

---

### 5. Reporting Format

**Q5.1:** What should a verification gap report contain?

**Q5.2:** What visualizations are most useful?
- [ ] Tables?
- [ ] Charts/plots?
- [ ] Heatmaps?
- [ ] Other?

**Q5.3:** What drill-down capabilities are needed?
- [ ] From system → element → property?
- [ ] From gap → source → recommendation?

**Q5.4:** How should the report be delivered?
- [ ] Web dashboard?
- [ ] PDF export?
- [ ] API endpoints?
- [ ] Email alerts?
- [ ] Other?

---

### 6. Integration Points

**Q6.1:** How does verification relate to other NFM features?
- [ ] Reference values (literature data)
- [ ] Gap fill system
- [ ] Quality gates
- [ ] Ontology/services

**Q6.2:** Are there existing verification standards or ontologies we should reference?

**Q6.3:** What external systems need to consume verification data?

---

## Expected Deliverables

### Primary Deliverables

**1. Verification Requirements Summary**
- Clear definition of "verification gap"
- Categorization framework (severity levels)
- Priority areas for data collection

**2. Stakeholder Requirements Document**
- Quality thresholds (confidence, uncertainty)
- Coverage requirements (which systems, what % complete)
- Reporting frequency expectations

**3. Current Process Analysis**
- Documentation of manual verification workflow
- Pain point inventory
- Automation opportunities

**4. Reporting Format Specification**
- Report structure/schema
- Visualization requirements
- Delivery mechanism preferences

### Secondary Deliverables

**5. Priority Ranking**
- Top 10 verification gaps to address first
- Rationale for prioritization (safety, performance, completeness)

**6. Data Source Mapping**
- Where to find missing verification data
- Quality assessment of potential sources

---

## Consultation Method

**Format:** Structured interview (documented)
**Duration:** 60-90 minutes estimated
**Location:** [To be determined]
**Recording:** [With consent] for accuracy

**Preparation materials for Lili:**
- NFM database schema (relevant tables)
- Current verification pipeline documentation (NFM-66)
- Reference values system overview (NFM-47)
- Gap fill system overview (NFM-40)

---

## Success Criteria

The consultation is successful when we have:

- [ ] **Clear definition** of "verification gap"
- [ ] **Prioritized list** of properties/systems needing verification
- [ ] **Stakeholder quality thresholds** documented
- [ ] **Reporting format requirements** specified
- [ ] **Understanding of current pain points** and automation opportunities

---

## Next Steps After Consultation

1. **Document findings** in structured format
2. **Create follow-up issues** for technical implementation:
   - Verification gap detection algorithm
   - Reporting API/dashboard
   - Automation of manual processes
3. **Validate requirements** with stakeholders
4. **Prioritize development** based on consultation insights

---

## Appendix: Related Issues

- **NFM-66:** Verification pipeline implementation
- **NFM-47:** Reference values research (78 refs, 207 total tests)
- **NFM-40:** Reference gap-fill system
- **NFM-22:** Literature pipeline (OntoFuel extraction)

---

## Notes from Consultation

*This section will be populated during/after the consultation with Lili.*

---

*Last updated: 2026-06-12*
