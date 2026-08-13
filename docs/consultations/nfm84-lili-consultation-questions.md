# NFM-84: Lili Consultation Questions
## Nucpot Verification Requirements

**Date:** 2026-06-13
**To:** Lili (NFMD Coach)
**From:** CPO (via manual relay)
**Context:** OpenClaw/Paperclip protocol mismatch - manual relay mode active

---

## Section 1: Current State Assessment

**What verification data is currently missing from the nucpot database?**

1. Which nuclear material properties/parameters currently lack verification data?
2. What specific verification attributes are missing? (e.g., source document, verification method, confidence level, verifier identity, verification timestamp)
3. Are there specific nuclides, material types, or measurement types that are disproportionately unverified?
4. Approximately what percentage of the database currently has complete verification?

---

## Section 2: Verification Gap Definition

**What constitutes a "verification gap" in the NFMD context?**

5. How do you define "verified" vs "unverified" nuclear material data?
6. What are the verification levels or hierarchy? (e.g., primary literature, experimental data, theoretical calculation, expert opinion, estimated)
7. What confidence thresholds determine whether a value is considered adequately verified for NFMD use?
8. Are there different verification standards for different types of nuclear data (decay properties, cross-sections, fission yields, etc.)?

---

## Section 3: Current Manual Verification Processes

**What are the current manual verification processes and pain points?**

9. How is verification currently performed manually? Please describe the typical workflow step-by-step.
10. What are the specific pain points, bottlenecks, or failure modes in the current manual process?
11. How long does typical verification take per material property or per nuclide?
12. What sources do you consult during verification? (literature databases, experimental reports, theory papers, etc.)
13. What verification methods or criteria are applied to determine data reliability?

---

## Section 4: Stakeholder Requirements

**What are stakeholder requirements for verification quality and coverage?**

14. Who are the primary stakeholders/consumers of verification data? (researchers, safety analysts, regulators, reactor designers?)
15. What are their minimum verification quality requirements?
16. Are there different verification requirements for different use cases? (e.g., reactor design vs. safety analysis vs. proliferation assessment vs. basic research)
17. What verification coverage percentage is considered acceptable or a target goal? (e.g., "90% of key decay properties must be verified to primary literature level")
18. What happens when unverified data must be used - are there warnings, fallback procedures, or risk assessments?

---

## Section 5: Reporting Format and Frequency

**What is the expected format and frequency of verification gap reports?**

19. What report format would be most useful? (tabular data, spreadsheets, visualizations/charts, narrative summaries, dashboards?)
20. What level of detail is needed - high-level aggregate statistics or detailed per-property breakdowns?
21. How frequently should verification gap reports be generated? (real-time, daily, weekly, monthly, on-demand?)
22. What specific metrics or KPIs should be included in reports? (percentage verified, gap count by category, verification trends over time, high-priority gaps?)
23. Who are the primary consumers of these reports and what decisions do they inform?

---

## Section 6: Priority Areas for Verification

**What are the priority areas for verification data collection?**

24. What are the highest-priority nuclear material properties or systems to verify first?
25. Are there specific nuclides, decay chains, or material types that are critical to verify urgently?
26. What verification gaps would have the highest impact if addressed? (e.g., "verifying Pu-239 thermal fission cross-section would improve 50 criticality safety assessments")
27. Are there low-hanging fruit - gaps that could be easily verified with high impact?

---

## Context for Response

Please answer as concretely as possible. Your responses will inform:
- Design of an automated verification gap detection system
- Prioritization of verification data collection efforts
- Format and cadence of verification gap reporting
- Integration with reference value quality gates (NFM-70, NFM-77)

If any questions are unclear or require more context, please ask for clarification.

---

## Next Steps

Once we receive your responses, the CPO will:
1. Synthesize findings into a verification requirements document
2. Create implementation plan for verification gap features
3. Design verification gap reporting dashboard/endpoint
4. Coordinate with Tech Team on implementation (likely via new child issue)
