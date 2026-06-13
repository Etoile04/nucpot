# NFM-87: AI Agent Hiring Decision

**Date**: 2026-06-13
**Author**: CEO
**Status**: Revised - AI Agent Approach
**Dependencies**: NFM-84 ✅, NFM-85 ✅, NFM-86 ✅
**Blocks**: NFM-83 final implementation

---

## Executive Summary

**Recommendation**: **Build a specialized Nuclear Materials Domain Expert AI agent** designed by Skills Architect and Workflow Designer.

**Decision**: Create an AI agent to handle verification database maintenance tasks, not hire human consultants.

**Rationale**:
- No budget for human domain consultants
- AI agent can handle 70-80% of domain tasks autonomously
- Skills Architect + Workflow Designer can build specialized capabilities
- Integrates with existing verification architecture (NFM-85)
- Scalable, consistent, and cost-effective

---

## Part 1: Revised Context - "Hiring" Means AI Agent

### 1.1 Clarification from User

> "No budget and no need to hire human experts. 'Hiring' means to create a specialized agent with skills and workflows designed by Skills Architect and Workflow Designer to fulfill the duty required."

**Implications**:
- **NOT** hiring human nuclear domain consultants
- **NOT** comparing human consultant costs ($236K hybrid model from previous analysis)
- **YES** building an AI agent with domain-specific capabilities
- **YES** leveraging internal team (Skills Architect + Workflow Designer)

### 1.2 Cost Framework Change

| Cost Category | Previous (Human) | Revised (AI Agent) |
|--------------|-----------------|-------------------|
| **Development** | N/A (hire existing) | Internal team time (Skills Architect + Workflow Designer) |
| **Operation** | $150-200/hour consultant | API costs per task (~$0.10-1 per domain query) |
| **3-Year Total** | $236,000 | ~$5,000-10,000 (API costs) |
| **Savings** | $259K vs full-time human | **Near-zero marginal cost** after development |

---

## Part 2: Domain Expertise Requirements (Unchanged)

### 2.1 Tasks Requiring Nuclear Domain Knowledge

From NFM-84 Section 5 ("Must Keep Human") and NFM-86 Part 2.3 ("Human-in-the-Loop Gates"):

| Task | Frequency | Domain Knowledge Required | AI Agent Feasibility |
|------|-----------|---------------------------|---------------------|
| **Reference value entry** | 10-20/month | Literature search, source validation, uncertainty estimation | **HIGH** - Agent can query databases, estimate uncertainty from source metadata |
| **F-grade adjudication** | 5-10/month | Debug LAMMPS failures, judge potential vs reference | **MEDIUM** - Agent can analyze failure patterns, suggest fixes |
| **Reference conflict resolution** | 2-5/month | Compare sources, select primary value, document rationale | **HIGH** - Agent can apply decision rules, rank sources |
| **Quarterly P0 audit** | 1/quarter | Comprehensive review of safety-critical data | **HIGH** - Agent can run systematic checks, flag anomalies |
| **New system first verification review** | 1-2/month | Sanity-check first-time results | **HIGH** - Agent can validate against expected ranges |
| **Non-EAM template selection** | Ad-hoc | Custom LAMMPS input design (Buckingham, Tersoff, AIREBO) | **MEDIUM** - Agent can select based on material properties |
| **Annual strategic review** | 1/year | Year-over-year analysis, target setting | **MEDIUM** - Agent can aggregate metrics, highlight trends |

### 2.2 Workload Quantification

**Monthly Domain Tasks**:
- Reference entry: 15 entries × 30 min = **7.5 hours** (human) → **5 min** (AI agent) = 1.25 hours/month
- F-grade adjudication: 8 cases × 20 min = **2.7 hours** (human) → **5 min** (AI agent) = 0.67 hours/month
- Conflict resolution: 3 cases × 30 min = **1.5 hours** (human) → **3 min** (AI agent) = 0.15 hours/month
- First verification reviews: 2 cases × 15 min = **0.5 hours** (human) → **2 min** (AI agent) = 0.07 hours/month
- **Total Monthly (Human)**: ~12 hours
- **Total Monthly (AI Agent)**: ~2 hours computation + human review

**AI Agent Efficiency**: 6x faster for routine tasks

**Human Oversight Required**: 20-30% of cases (complex F-grade, edge cases)

---

## Part 3: AI Agent Design

### 3.1 Agent Architecture

**Agent Name**: `nuclear-domain-expert`

**Core Capabilities**:
1. **Literature Search & Validation**
   - Query NIST IPR, OpenKIM, Materials Project
   - Validate source credibility
   - Extract uncertainty ranges

2. **LAMMPS Failure Analysis**
   - Parse error messages
   - Identify common failure patterns (NaN, instability, convergence issues)
   - Suggest potential fixes (potential adjustments, timestep changes)

3. **Reference Conflict Resolution**
   - Apply decision rules (experimental > DFT, recent > old, primary > secondary)
   - Document rationale
   - Flag for human review if confidence < 80%

4. **Quarterly Audit Execution**
   - Systematic P0 data quality checks
   - Generate audit report
   - Flag anomalies for human review

5. **LAMMPS Template Selection**
   - Match material properties to potential types
   - Select EAM if available, fallback to Buckingham/Tersoff/AIREBO
   - Generate input file template

### 3.2 Skills Required

**Skills Architect to Design**:

1. **nuclear-materials-knowledge** (Domain knowledge retrieval)
   - Knowledge base: U, UO₂, Zr, Fe, U-Zr crystal structures
   - LAMMPS potential types and use cases
   - Common failure patterns and fixes

2. **literature-search** (Source validation)
   - Query NIST IPR, OpenKIM, Materials Project APIs
   - Credibility scoring
   - Uncertainty extraction

3. **lammps-debugger** (Failure analysis)
   - Error pattern recognition
   - Suggest fixes based on error type
   - Validate proposed solutions

4. **quality-audit** (Audit execution)
   - P0 data quality checklist
   - Anomaly detection
   - Report generation

5. **template-selector** (LAMMPS input design)
   - Material property → potential type mapping
   - Template generation
   - Parameter estimation

**Workflow Designer to Design**:

1. **reference-validation-workflow**
   - Receive reference candidate
   - Literature search → source validation → uncertainty estimation
   - Return validated reference with confidence score

2. **f-grade-adjudication-workflow**
   - Receive F-grade case
   - LAMMPS log analysis → pattern match → suggest fix
   - If confidence < 70%, escalate to human (Lili consultation backup)

3. **quarterly-audit-workflow**
   - Query all P0 systems
   - Run quality checks (uncertainty coverage, recent verification, conflict detection)
   - Generate PDF report + flag issues

### 3.3 Integration with NFM-85 Architecture

From NFM-85 (CTO evaluation), the verification system has:

```
┌─────────────────────────────────────────────────────────────┐
│                  Verification API Layer                     │
│  POST /api/v1/verification/check-gap                        │
│  POST /api/v1/verification/adjudicate-grade                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Domain Expert Service (NEW)                    │
│  - Reference validation                                      │
│  - F-grade adjudication                                     │
│  - Conflict resolution                                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Nuclear Domain Expert Agent                    │
│  - Skills: nuclear-materials-knowledge, literature-search  │
│          lammps-debugger, quality-audit, template-selector │
│  - Workflows: reference-validation, f-grade-adjudication   │
│                quarterly-audit                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              External Data Sources                          │
│  - NIST IPR, OpenKIM, Materials Project                    │
└─────────────────────────────────────────────────────────────┘
```

**Integration Points**:
- Verification API calls Domain Expert Service
- Domain Expert Service invokes Nuclear Domain Expert Agent
- Agent queries external databases, applies domain logic
- Returns structured result (validated reference, adjudication decision)

---

## Part 4: Revised Hiring Options

### 4.1 Option A: Build Nuclear Domain Expert Agent (RECOMMENDED)

**Specification**:
- **Build Team**: Skills Architect + Workflow Designer (internal)
- **Development Time**: 2-3 weeks
- **Skills**: 5 specialized skills
- **Workflows**: 3 integrated workflows
- **Integration**: Connect to NFM-85 verification API

**Cost**:
- Development: Internal team time (no direct cost)
- Operation: API costs (~$0.10-1 per domain query)
- Monthly API cost: ~$50-100 (assuming 500-1000 domain tasks/month)
- **3-Year Total**: ~$5,000-10,000

**Pros**:
- **Zero marginal cost** after development
- **6x faster** than human for routine tasks
- **Consistent** application of domain rules
- **Scalable** to increased workload
- **Always available** (no scheduling)
- **Integrates** with existing architecture

**Cons**:
- Requires upfront development effort
- Cannot handle 100% of cases (20-30% need human review)
- Requires Lili consultation backup for complex cases

**Verdict**: **RECOMMEND** - Best fit for no-budget constraint, scalable, efficient

### 4.2 Option B: Use General-Purpose AI (Claude/GPT) with Prompting

**Specification**:
- Use existing Claude/GPT API
- Provide nuclear materials context in system prompt
- No specialized skills/workflows

**Cost**:
- API costs: Similar to Option A
- No development cost
- **3-Year Total**: ~$5,000-10,000

**Pros**:
- Faster to deploy (no development)
- Can handle ad-hoc queries

**Cons**:
- Less reliable for specialized domain tasks
- No persistent domain knowledge
- Inconsistent application of rules
- Higher error rate on complex tasks

**Verdict**: **FALLBACK** - Use for exploration, but Option A is better for production

### 4.3 Option C: Manual Workflow (No Agent)

**Specification**:
- Workflow Designer builds manual workflows
- Internal team handles domain tasks manually
- Use Lili consultation for complex cases

**Cost**:
- Internal team time: 12 hours/month
- Lili consultation: 2-3 hours/month for complex cases
- **3-Year Total**: Internal team opportunity cost

**Pros**:
- No development effort
- Full human control

**Cons**:
- **Slow**: 12 hours/month internal team time
- **Inconsistent**: Domain knowledge spread across team
- **Not scalable**: Doesn't handle increased workload

**Verdict**: **INTERIM** - Acceptable while building Option A, not sustainable long-term

---

## Part 5: Recommended Implementation Plan

### 5.1 Phase 1: Agent Development (Weeks 1-3)

**Week 1: Domain Knowledge Base**
- Skills Architect creates `nuclear-materials-knowledge` skill
- Compile crystal structure database (U, UO₂, Zr, Fe, U-Zr)
- Document LAMMPS potential types and use cases
- Create failure pattern library

**Week 2: Core Skills**
- Skills Architect builds:
  - `literature-search` (NIST IPR, OpenKIM, Materials Project APIs)
  - `lammps-debugger` (error pattern recognition)
  - `quality-audit` (P0 checklist, anomaly detection)
- Workflow Designer designs:
  - `reference-validation-workflow` (end-to-end reference entry)

**Week 3: Workflows & Integration**
- Workflow Designer builds:
  - `f-grade-adjudication-workflow`
  - `quarterly-audit-workflow`
- CTO integrates agent with NFM-85 verification API
- End-to-end testing

**Deliverables**:
- `nuclear-domain-expert` agent with 5 skills + 3 workflows
- Integration with verification API
- Test cases for each workflow

### 5.2 Phase 2: Deployment & Monitoring (Month 2)

**Week 1: Soft Launch**
- Deploy agent to staging environment
- Run test cases (20 reference entries, 10 F-grade cases)
- Measure: accuracy, speed, confidence scores

**Week 2-3: Production Rollout**
- Deploy to production
- Handle 50% of domain tasks (agent) + 50% manual (parallel operation)
- Monitor: error rate, escalation rate, human override rate

**Week 4: Optimization**
- Analyze first month performance
- Refine skills/workflows based on edge cases
- Target: <20% escalation rate, >80% confidence on handled cases

**Deliverables**:
- Production deployment
- Performance metrics dashboard
- Optimization plan

### 5.3 Phase 3: Steady State (Month 3+)

**Operation**:
- Agent handles 70-80% of domain tasks autonomously
- Human review for 20-30% (complex F-grade, low confidence)
- Quarterly audit fully automated (human reviews report)
- Lili consultation backup for escalations

**Monitoring**:
- Weekly: Error rate, escalation rate
- Monthly: Confidence distribution, human feedback
- Quarterly: Full audit quality review

**Continuous Improvement**:
- Skills Architect updates skills based on new patterns
- Workflow Designer refines workflows based on feedback
- Add new capabilities as needed (e.g., new material systems)

---

## Part 6: Success Metrics

### 6.1 Phase 1 Success Criteria (Development)

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Skills Completed** | 5 skills | nuclear-materials-knowledge, literature-search, lammps-debugger, quality-audit, template-selector |
| **Workflows Completed** | 3 workflows | reference-validation, f-grade-adjudication, quarterly-audit |
| **Integration** | API connected | Verification API calls Domain Expert Service |
| **Test Coverage** | >90% | All workflows have test cases |

### 6.2 Phase 2 Success Criteria (Deployment)

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Accuracy** | >85% | % of agent decisions correct (vs human review) |
| **Escalation Rate** | <20% | % of cases escalated to human |
| **Confidence Score** | >80% | Average confidence on non-escalated cases |
| **Speed** | <5 min per task | Average time for reference validation |

### 6.3 Phase 3 Success Criteria (Steady State)

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Autonomous Handling** | >70% | % of domain tasks handled without human review |
| **P0 Quality** | 100% | All P0 references have uncertainty coverage |
| **Quarterly Audit** | 100% | 4/4 quarters automated, reports generated |
| **Cost Efficiency** | <$100/month | API costs for domain tasks |
| **Human Time Saved** | >8 hours/month | Reduction from 12 hours to <4 hours/month |

---

## Part 7: Risk Mitigation

### 7.1 Risk Register

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| **Agent hallucinates** domain knowledge | Medium | High | Confidence scoring, human review for low confidence, source citation | Skills Architect |
| **LAMMPS failure patterns** not in library | Medium | Medium | Continuous learning, add new patterns, escalate unknown cases | Skills Architect |
| **API rate limits** on external sources | Low | Medium | Caching, batch queries, fallback to Lili consultation | CTO |
| **Integration fails** with NFM-85 | Low | High | Thorough testing, staging rollout, rollback plan | CTO |
| **Escalation rate** >30% | Medium | Medium | Refine skills/workflows, add more domain knowledge | Skills Architect |

### 7.2 Backup Plan

**If Agent Fails** (escalation rate >40%, accuracy <70%):

1. **Immediate**: Fall back to Option C (manual workflow)
2. **Short-term**: Use Option B (general AI with prompting) for exploration
3. **Long-term**: Rebuild agent with improved domain knowledge

**Trigger**: Two consecutive weeks with escalation rate >40% OR accuracy <70%

**Lili Consultation Backup**:
- For complex F-grade cases
- For non-EAM template selection
- For quarterly audit review
- Already tested in NFM-89

---

## Part 8: Cost-Benefit Analysis (Revised)

### 8.1 Development Cost

| Cost Item | One-Time | Recurring | Notes |
|-----------|----------|-----------|-------|
| **Skills Architect** | 80 hours | 0 | $0 (internal team) |
| **Workflow Designer** | 40 hours | 0 | $0 (internal team) |
| **CTO Integration** | 20 hours | 0 | $0 (internal team) |
| **Total Development** | **140 hours** | **$0** | Internal team time |

### 8.2 Operation Cost

| Cost Item | Monthly | Yearly | 3-Year |
|-----------|---------|--------|-------|
| **API Calls** (domain queries) | $50-100 | $600-1,200 | $1,800-3,600 |
| **External APIs** (NIST, OpenKIM) | $0 | $0 | $0 (free tiers) |
| **Lili Consultation** (escalations) | $50 | $600 | $1,800 (backup) |
| **Total Operation** | **$100-150** | **$1,200-1,800** | **$3,600-5,400** |

### 8.3 Comparison with Previous Human Consultant Analysis

| Model | Development | Year 1 | Year 2 | Year 3 | 3-Year Total |
|-------|-------------|--------|--------|--------|--------------|
| **Human Consultant** (previous analysis) | $0 | $96,000 | $72,000 | $68,000 | $236,000 |
| **AI Agent** (revised) | $0 | $1,200 | $1,800 | $1,800 | $4,800 |
| **Savings** | $0 | **$94,800** | **$70,200** | **$66,200** | **$231,200** |

**Note**: Human consultant model is no longer applicable per user clarification ("No budget and no need to hire human experts").

### 8.4 Time Savings

| Metric | Human | AI Agent | Savings |
|--------|-------|----------|---------|
| **Reference validation** | 7.5 hours/month | 1.25 hours/month | 6.25 hours |
| **F-grade adjudication** | 2.7 hours/month | 0.67 hours/month | 2.03 hours |
| **Conflict resolution** | 1.5 hours/month | 0.15 hours/month | 1.35 hours |
| **Reviews & audits** | 0.5 hours/month | 0.07 hours/month | 0.43 hours |
| **Total Monthly** | **12 hours** | **2 hours** | **10 hours** |
| **Annual Time Saved** | 144 hours | 24 hours | **120 hours** |

---

## Part 9: Conclusion

### 9.1 Final Recommendation

**Decision**: **Build Nuclear Domain Expert AI Agent**

**Approach**: Skills Architect + Workflow Designer design specialized agent

**Justification**:

1. **No budget constraint**: Agent costs ~$5K over 3 years vs $236K for human consultant
2. **Scalable**: Handles 70-80% of domain tasks autonomously
3. **Consistent**: Applies domain rules uniformly
4. **Fast**: 6x faster than human for routine tasks
5. **Integrates**: Connects to NFM-85 verification architecture
6. **Always available**: No scheduling, 24/7 operation

### 9.2 Next Steps

1. **Skills Architect**: Design 5 domain skills (Week 1-2)
2. **Workflow Designer**: Design 3 workflows (Week 2-3)
3. **CTO**: Integrate agent with NFM-85 verification API (Week 3)
4. **Testing**: Staging deployment, test cases (Week 4)
5. **Production**: Gradual rollout, monitor performance (Month 2)
6. **Unblocks NFM-83**: Proceed with final implementation using AI agent

### 9.3 Issue Disposition

**NFM-87 Status**: ✅ **COMPLETE** - AI agent hiring recommendation delivered

**Deliverables**:
- ✅ Workload requirements assessment (15-16 hours/month human → 2 hours/month AI)
- ✅ Domain expertise specification (5 skills, 3 workflows)
- ✅ Revised hiring options (AI agent vs general AI vs manual)
- ✅ Recommended AI agent approach ($4,800 vs $236,000)
- ✅ Implementation plan (3-phase: development, deployment, steady state)
- ✅ Success metrics and risk mitigation

**Follow-up Issues** (CREATED 2026-06-13):
1. **NFM-97** (NFM-87.1): Build 5 domain skills → `78e07624` → Skills Architect (07090cce) — **todo**
2. **NFM-98** (NFM-87.2): Build 3 verification workflows → `74c8192a` → Workflow Designer (6fbeddcd) — **todo**
3. **NFM-99** (NFM-87.3): Integrate with NFM-85 verification API → `61eb2fe3` → CTO (3a0e0b92) — **todo**
4. **NFM-83**: Final implementation unblocked (proceed once NFM-97/98/99 complete)

**Dependency Chain**: NFM-97 (skills) → NFM-98 (workflows) → NFM-99 (integration) → NFM-83 (final)

---

*Document Status: HANDOFF COMPLETE — Child issues created, board notified*
*Next Action: Skills Architect begins NFM-97 (5 domain skills)*
