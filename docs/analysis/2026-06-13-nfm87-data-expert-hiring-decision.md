# NFM-87: Data Expert Hiring Decision

**Date**: 2026-06-13
**Author**: CEO
**Status**: Complete
**Dependencies**: NFM-84 ✅, NFM-85 ✅, NFM-86 ✅
**Blocks**: NFM-83 final implementation

---

## Executive Summary

**Recommendation**: **DO NOT hire a full-time data expert**. Instead, adopt a **hybrid model** combining part-time nuclear domain consulting with internal team cross-training.

**Decision**: Alternative approach over dedicated hire

**Rationale**: After analyzing NFM-84, NFM-85, and NFM-86 outputs, the domain expertise workload is:
- **Sporadic** (not daily)
- **Specialized** (nuclear materials domain knowledge)
- **Reducable** via automation and process design
- **Scalable** via documentation and cross-training

---

## Part 1: Domain Expertise Requirements Analysis

### 1.1 Tasks Requiring Nuclear Domain Knowledge

Based on NFM-84 Section 5 ("Must Keep Human") and NFM-86 Part 2.3 ("Human-in-the-Loop Gates"):

| Task | Frequency | Estimated Time | Domain Knowledge Required |
|------|-----------|----------------|---------------------------|
| **Reference value entry** | 10-20/month | 30 min each | Literature search, source validation, uncertainty estimation |
| **F-grade adjudication** | 5-10/month | 20 min each | Debug LAMMPS failures, judge potential vs reference |
| **Reference conflict resolution** | 2-5/month | 30 min each | Compare sources, select primary value, document rationale |
| **Quarterly P0 audit** | 1/quarter | 8-10 hours | Comprehensive review of safety-critical data |
| **New system first verification review** | 1-2/month | 15 min each | Sanity-check first-time results |
| **Non-EAM template selection** | Ad-hoc | 1-2 hours each | Custom LAMMPS input design (Buckingham, Tersoff, AIREBO) |
| **Annual strategic review** | 1/year | 4 hours | Year-over-year analysis, target setting |

### 1.2 Workload Quantification

**Monthly Time Estimate**:
- Reference entry: 15 entries × 30 min = **7.5 hours**
- F-grade adjudication: 8 cases × 20 min = **2.7 hours**
- Conflict resolution: 3 cases × 30 min = **1.5 hours**
- First verification reviews: 2 cases × 15 min = **0.5 hours**
- **Total Monthly Core Tasks**: ~12 hours

**Quarterly Time Estimate**:
- Quarterly audit: **8-10 hours** (once per quarter)
- Spread over 3 months: ~3 hours/month equivalent

**Annual Time Estimate**:
- Annual strategic review: **4 hours** (once per year)
- Spread over 12 months: ~20 minutes/month equivalent

**Total Sustained Monthly Workload**: **15-16 hours/month**

**Peak Workload** (new system onboarding, audit months): **25-30 hours/month**

### 1.3 Domain Knowledge Specialization

**Required Expertise**:
- Nuclear materials crystal structures (U, UO₂, Zr, Fe, U-Zr, etc.)
- LAMMPS potential types (EAM, MEAM, Buckingham, Tersoff, AIREBO, ReaxFF)
- Materials property calculation methods (DFT, experimental)
- Uncertainty estimation for different measurement techniques
- Literature sources (NIST IPR, OpenKIM, Materials Project, nuclear journals)

**Expertise Level**: Specialized domain knowledge (PhD-level nuclear materials science or equivalent experience)

**Market Availability**: Limited - nuclear materials experts are rare, typically work at national labs or academia

---

## Part 2: Hiring Options Evaluated

### 2.1 Option A: Full-Time Data Expert Hire

**Specification**:
- Role: Nuclear Materials Data Specialist
- FTE: 1.0 (40 hours/week)
- Salary: $120,000-150,000/year (PhD domain specialist)
- Benefits: 20% overhead = $24,000-30,000/year
- **Total Annual Cost**: $144,000-180,000

**Pros**:
- Dedicated owner of domain quality
- Available for ad-hoc questions
- Can grow with system complexity
- Institutional knowledge retention

**Cons**:
- **Underutilized**: 16 hours/month of actual work = 77% idle time
- **Overqualified**: Routine tasks don't require PhD-level expertise daily
- **Expensive**: $300-400/hour effective rate for actual work done
- **Retention risk**: Specialist may leave due to lack of challenging work
- **Scalability issue**: Single point of dependency

**Verdict**: **REJECT** - Poor fit for sporadic, specialized workload

### 2.2 Option B: Part-Time Consultant (Recommended)

**Specification**:
- Role: Nuclear Materials Domain Consultant
- Hours: 10 hours/week (40 hours/month)
- Rate: $150-200/hour
- **Total Annual Cost**: $72,000-96,000 (with buffer for peak months)

**Scope**:
- P0 reference value validation (peer review all P0 entries)
- F-grade adjudication for P0/P1 systems
- Quarterly P0 audit (8-10 hours)
- On-demand consultation for edge cases
- SOP development for reference validation

**Pros**:
- **Cost-effective**: $150-200/hour vs $300-400/hour equivalent for full-time
- **Right-sized**: Matches actual workload (16 hours/month core + buffer)
- **Specialized expertise**: Pay PhD-level rates only for PhD-level work
- **Flexible**: Scale up/down based on workload
- **No retention risk**: Consultant model expects sporadic engagement

**Cons**:
- Not always available (response time SLA needed)
- Less institutional continuity (mitigated via documentation)
- Limited capacity for unexpected growth

**Verdict**: **RECOMMEND** - Best fit for specialized, sporadic workload

### 2.3 Option C: Leverage External Partners (Alternative)

**Specification**:
- Partnerships with national labs (LANL, ORNL, INL)
- Collaboration agreements with university nuclear engineering programs
- Guest researcher arrangements

**Annual Cost**: Variable (in-kind support, joint projects)

**Pros**:
- Access to world-class domain expertise
- Potential for joint publications
- No direct hiring cost
- Reputation and credibility boost

**Cons**:
- Response time may be slow
- Alignment of priorities (our needs vs their research agenda)
- IP and publication considerations
- Less control over availability

**Verdict**: **SUPPLEMENTAL** - Good for strategic collaboration, not operational support

### 2.4 Option D: Cross-Train Internal Team (Hybrid Approach)

**Specification**:
- Train existing Workflow Designer in nuclear materials basics
- Develop detailed SOPs for all domain judgment tasks
- Create decision trees for common scenarios
- Use consultant for initial SOP design + periodic review

**Training Investment**:
- Initial training: 20 hours (consultant-led)
- Quarterly refreshers: 4 hours
- SOP development: 40 hours (consultant + internal team)

**Pros**:
- Builds internal capability long-term
- Reduces dependency on external consultant over time
- Workflow Designer gains domain context for process design
- Cost-effective after initial investment

**Cons**:
- 6-12 month ramp-up period
- Risk of incomplete knowledge transfer
- May still need consultant for complex cases

**Verdict**: **RECOMMEND as complement to Option B** - Build internal capability over time

---

## Part 3: Recommended Hybrid Model

### 3.1 Model Design

**Phase 1: Initial Setup (Months 1-3)**

**Consultant Engagement**:
- **Hours**: 20 hours/month (higher initial load)
- **Focus**:
  - Develop SOPs for all 5 human-in-the-loop gates
  - Design decision trees for reference validation
  - Create training materials for internal team
  - Validate first P0 reference values
  - Conduct first quarterly audit

**Internal Team**:
- Workflow Designer dedicates 10 hours/week to:
  - Learn nuclear materials basics
  - Shadow consultant on domain tasks
  - Implement SOPs in workflow
  - Build quality tracking dashboards

**Deliverables**:
- 5 SOP documents (one per human gate)
- Decision tree diagrams for common scenarios
- Training presentation with examples
- First batch of validated P0 reference values

---

**Phase 2: Transition (Months 4-12)**

**Consultant Engagement**:
- **Hours**: 10 hours/month (sustained)
- **Focus**:
  - Peer review P0 reference entries (quality gate)
  - F-grade adjudication for complex cases (escalated by team)
  - Quarterly P0 audits (8-10 hours each)
  - On-demand consultation for edge cases
  - Annual SOP review and updates

**Internal Team**:
- Workflow Designer handles independently:
  - P1-P3 reference validation (using SOPs)
  - Routine F-grade adjudication (clear-cut cases)
  - Weekly quality monitoring
  - Stakeholder notification management

**Escalation Criteria** (when to call consultant):
- Any P0 reference value with conflicting sources
- F-grade where cause is unclear after SOP troubleshooting
- Non-EAM potential template request
- New system onboarding (U-Pu-Zr, UN, UC, SiC)
- Quarterly audit (always consultant-led)

---

**Phase 3: Steady State (Year 2+)**

**Consultant Engagement**:
- **Hours**: 8 hours/month (reduced as internal capability matures)
- **Focus**:
  - Quarterly P0 audits (6-8 hours)
  - Complex escalation support (2 hours/month)
  - Annual strategic review participation

**Internal Team**:
- Handles all routine domain tasks independently
- Consultant role shifts from "doer" to "reviewer"
- Quarterly audits become primary consultant touchpoint

---

### 3.2 Cost Comparison

| Model | Year 1 Cost | Year 2 Cost | Year 3 Cost | 3-Year Total |
|-------|-------------|-------------|-------------|--------------|
| Full-time hire (Option A) | $160,000 | $165,000 | $170,000 | $495,000 |
| Hybrid model (Options B + D) | $96,000 | $72,000 | $68,000 | $236,000 |
| **Savings** | **-$64,000** | **-$93,000** | **-$102,000** | **-$259,000** |

**Cost Drivers**:
- Hybrid: Consultant hours decrease as internal team capability grows
- Full-time: Salary increases + benefits inflation
- Hybrid: No benefits overhead for consultant

---

### 3.3 Service Level Agreement (SLA) for Consultant

**Response Time Commitments**:

| Request Type | Response SLA | Availability |
|--------------|--------------|--------------|
| P0 reference validation | 24 hours | 5 business days/week |
| F-grade escalation (P0) | 48 hours | 5 business days/week |
| Quarterly audit scheduling | 1 week | Flexible around academic calendar |
| Emergency consultation (P0 SLA breach) | 4 hours | Best-effort (backup: Lili consultation) |

**Deliverables**:
- Monthly activity report (hours spent, tasks completed)
- Quarterly audit report (PDF + presentation)
- Annual SOP review and update
- On-demand training for new team members

**Quality Metrics**:
- P0 reference validation: 100% completeness check
- F-grade adjudication: 90% resolution rate (remaining escalated to Lili)
- Quarterly audit: 100% P0 systems covered

---

## Part 4: Implementation Plan

### 4.1 Immediate Actions (Week 1-2)

**1. Issue NFM-87.1: Find Nuclear Domain Consultant**
- Owner: CPO
- Deliverables: 2-3 consultant candidates, rate quotes, availability
- Sources: National lab alumni, nuclear engineering PhD graduates, Lili's network

**2. Issue NFM-87.2: Develop SOP Template**
- Owner: Workflow Designer
- Deliverables: SOP template with decision tree format
- Template sections: Scope, Prerequisites, Decision Steps, Quality Check, Escalation

**3. Issue NFM-87.3: Budget Allocation**
- Owner: CEO
- Deliverables: $96,000 budget approval for Year 1 consultant engagement
- Account: Professional services / Data quality consulting

### 4.2 Short-Term Actions (Month 1-3)

**NFM-87.4: Consultant Onboarding**
- Contract signing (10 hours/month minimum, 20 hours/month initial)
- Initial training session with Workflow Designer
- First P0 reference validation (consultant-led, team shadowing)

**NFM-87.5: SOP Development**
- SOP 1: Reference value entry validation
- SOP 2: F-grade adjudication
- SOP 3: Reference conflict resolution
- SOP 4: Non-EAM template selection
- SOP 5: Quarterly P0 audit

**NFM-87.6: Internal Team Training**
- Workflow Designer completes nuclear materials basics training
- Practice sessions with real cases (consultant-supervised)
- Sign-off on SOP understanding

### 4.3 Medium-Term Actions (Month 4-12)

**NFM-87.7: Transition to Independent Operation**
- Workflow Designer takes over P1-P3 reference validation
- Consultant shifts to peer-review role for P0
- Quarterly audit #1 (consultant-led, Workflow Designer assists)

**NFM-87.8: Process Refinement**
- Collect feedback on SOP effectiveness
- Adjust escalation criteria based on actual cases
- Fine-tune consultant hours (target: 10 hours/month)

**NFM-87.9: Annual Review**
- Year 1 cost vs benefit analysis
- Consultant performance review
- Decision: renew, adjust, or rebid for Year 2

---

## Part 5: Success Metrics

### 5.1 Year 1 Success Criteria

| Metric | Target | Measurement |
|--------|--------|-------------|
| **P0 Reference Quality** | 100% uncertainty coverage | % of P0 values WITH uncertainty IS NOT NULL |
| **P0 SLA Compliance** | ≥95% | % of P0 gaps verified within 24 hours |
| **F-grade Resolution Rate** | ≥90% | % of F-grade cases resolved without escalation |
| **Quarterly Audit Completion** | 100% | 4/4 quarters completed on schedule |
| **Cost Efficiency** | <$100,000 Year 1 | Total consultant spend |
| **Internal Capability** | Workflow Designer certified | Signed off on all 5 SOPs |

### 5.2 Year 2+ Success Criteria

| Metric | Target | Measurement |
|--------|--------|-------------|
| **P0 Reference Quality** | 100% uncertainty coverage | Maintain Year 1 standard |
| **Quarterly Audit Completion** | 100% | 4/4 quarters completed on schedule |
| **Cost Efficiency** | <$75,000 Year 2 | Reduced consultant hours |
| **Internal Capability** | 80% of tasks done internally | % of domain tasks without consultant involvement |
| **Escalation Rate** | <20% | % of domain tasks escalated to consultant |

---

## Part 6: Risk Mitigation

### 6.1 Risk Register

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| **Consultor unavailable** during P0 emergency | Medium | High | Backup: Lili consultation (NFM-89 gateway fix) | CPO |
| **SOPs insufficient** for complex cases | Medium | Medium | Quarterly SOP review, escalate edge cases | Workflow Designer |
| **Internal team leaves** before knowledge transfer | Low | High | Document all SOPs in shared drive, cross-train backup | CPO |
| **Consultor rate increases** significantly | Low | Medium | 3-year preferred rate clause, budget for 20% increase | CEO |
| **Workload exceeds** consultant capacity | Low | Medium | 20-hour/month buffer built into budget, can scale | CPO |

### 6.2 Backup Plan

**If Consultant Model Fails** (escalation rate >30%, consultant unavailable >2 weeks):

1. **Immediate**: Engage Lili for emergency consultation (already tested in NFM-84)
2. **Short-term**: Hire interim consultant at $250/hour rush rate
3. **Long-term**: Re-evaluate full-time hire vs consultant market

**Trigger**: Two consecutive quarters with escalation rate >30% OR consultant unavailable >4 weeks total

---

## Part 7: Conclusion

### 7.1 Final Recommendation

**Decision**: **DO NOT hire full-time data expert**

**Adopt**: **Hybrid model (Option B + D)** - Part-time nuclear domain consultant + internal team cross-training

**Justification**:

1. **Workload doesn't justify FTE**: 15-16 hours/month sustained = 40% of one FTE
2. **Specialization is narrow**: Nuclear materials expertise needed for 5 specific judgment tasks
3. **Cost savings**: $259,000 over 3 years vs full-time hire
4. **Scalability**: Consultant hours scale with actual workload
5. **Risk mitigation**: Builds internal capability over time, reduces dependency

### 7.2 Next Steps

1. **Board Approval**: Authorize $96,000 budget for Year 1 consultant engagement
2. **Issue NFM-87.1**: CPO to find nuclear domain consultant (2-3 candidates)
3. **Issue NFM-87.2**: Workflow Designer to develop SOP template
4. **Unblock NFM-83**: Proceed with final NFM-83 implementation using hybrid model

### 7.3 Issue Disposition

**NFM-87 Status**: ✅ **COMPLETE** - Hiring recommendation delivered

**Deliverables**:
- ✅ Workload requirements assessment (15-16 hours/month)
- ✅ Domain expertise specification
- ✅ Four hiring options evaluated (A: full-time, B: consultant, C: partners, D: cross-train)
- ✅ Recommended hybrid model (B + D) with 3-year cost savings ($259,000)
- ✅ Implementation plan (NFM-87.1 through NFM-87.9)
- ✅ Success metrics and risk mitigation

**Follow-up Issues**:
1. **NFM-87.1**: Find nuclear domain consultant (CPO)
2. **NFM-87.2**: Develop SOP template (Workflow Designer)
3. **NFM-87.3**: Budget allocation approval (CEO)
4. **NFM-83**: Final implementation unblocked (proceed with hybrid model)

---

*Document Status: COMPLETE - Ready for board review*
*Next Action: Board approval of hybrid model and budget*
