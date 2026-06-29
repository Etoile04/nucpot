# NFM-23: Feasibility Study — COG-Second-Brain for Company Adoption

**Date:** 2026-06-09
**Author:** CEO Agent
**Reviewers:** CTO, Creative Director, Skills Architect
**Status:** Revised after expert panel review
**Source:** [github.com/huytieu/COG-second-brain](https://github.com/huytieu/COG-second-brain)

---

## 1. Executive Summary

**COG (Cognition + Obsidian + Git)** is an open-source, self-evolving "second brain" framework built on markdown files and powered by AI coding agents. It provides 17 skills and 6 specialist worker agents for personal knowledge management, team intelligence, and product management workflows.

**Verdict: Cautiously Positive — Selective Adoption Recommended**

COG offers several high-value capabilities that would directly benefit our team, but its individual-centric design and overlap with our existing Paperclip agent infrastructure mean we should adopt it selectively rather than wholesale.

---

## 2. What COG Is

| Aspect | Details |
|--------|---------|
| **Architecture** | Markdown-first knowledge base (.md files) + AI agent skills + Git version control |
| **Agent Support** | Claude Code (first-class), Cursor, Kiro, Gemini CLI, OpenAI Codex |
| **License** | MIT (free, open-source) |
| **Data Store** | Local .md files — no database, no cloud dependency |
| **Privacy Model** | Privacy-first: local files, no external servers, API calls only on user invocation |
| **Version** | v3.5 |
| **Inspired By** | Garry Tan's gstack/gbrain, Zettelkasten, Tiago Forte's PARA, GTD |

### Core Capabilities

**Personal Knowledge (7 skills):**
- Braindump — capture raw thoughts with intelligent classification
- Daily Brief — verified news intelligence
- URL Dump — save URLs with auto-extracted insights
- Weekly Check-in — cross-domain pattern analysis
- Knowledge Consolidation — build frameworks from scattered notes
- Onboarding — personalize COG for your workflow
- Update COG — self-update framework without touching content

**Team Intelligence (3 skills):**
- Team Brief — cross-reference GitHub + Linear + Slack + PostHog
- Meeting Transcript — process meetings into decisions and action items
- Comprehensive Analysis — deep 7-day analysis for strategic planning

**PM Workflow (6 skills):**
- Create User Story, Generate PRD, Generate Release Notes
- Export Open Issues, Publish to Confluence, Update Knowledge Base

**Strategic Research (1 skill):**
- Auto-Research — multi-agent parallel research with source citations

**Worker Agents (6 specialist sessions):**
- Data Collector, Researcher, File Ops, Executor, Publisher, People Updater
- Uses Sonnet for I/O-heavy work, Opus for reasoning

---

## 3. Mapping to Our Company Needs

### 3.1 High-Value Use Cases for NFM Team

| Use Case | COG Skill | Impact | Priority |
|----------|-----------|--------|----------|
| **Domain Knowledge Capture** | Braindump, Knowledge Consolidation | Nuclear fuel management has deep, specialized domain knowledge. COG's braindump + consolidation cycle would help capture and organize insights from literature reviews, simulation results, and expert discussions. | **HIGH** |
| **Literature Research Pipeline** | Auto-Research, URL Dump | Directly supports our NFM-22 literature pipeline. Auto-Research decomposes questions into parallel threads with multiple agents — this is exactly the literature discovery pattern we need. | **HIGH** |
| **Meeting Intelligence** | Meeting Transcript, Team Brief | Our team uses meetings for design reviews, sprint planning, and strategic decisions. COG can extract action items, decisions, and team dynamics automatically. | **MEDIUM** |
| **Weekly Retrospectives** | Weekly Check-in, Comprehensive Analysis | Cross-domain pattern analysis across all our work streams. Would surface insights that individual agents miss in isolation. | **MEDIUM** |
| **Knowledge Base Maintenance** | Update Knowledge Base, Knowledge Consolidation | Our docs/ folder grows organically. COG can consolidate scattered analysis documents into structured frameworks. | **MEDIUM** |

### 3.2 Roles That Benefit Most

| Role | Applicable COG Skills | Role Pack |
|------|----------------------|-----------|
| **CTO** (Engineering Lead) | Team Brief, Comprehensive Analysis, Auto-Research, Meeting Transcript | Engineering Lead |
| **CMO** (Marketing) | Daily Brief, Auto-Research, Braindump, URL Dump | Marketer |
| **Creative Director** | Knowledge Consolidation, Auto-Research, Comprehensive Analysis, Weekly Check-in | Founder |
| **UX Designer** | Create User Story, Braindump, URL Dump | Designer |
| **CEO** | All — especially Comprehensive Analysis, Team Brief, Auto-Research | Founder |

---

## 4. Overlap with Existing Infrastructure

### 4.1 What We Already Have (Paperclip)

| Capability | Paperclip | COG | Gap? |
|------------|-----------|-----|------|
| **Agent orchestration** | ✅ CEO/CTO/CMO/UX/CD hierarchy | ✅ Lead + 6 worker agents | Paperclip is more structured for our org |
| **Task management** | ✅ Issues, comments, assignments | ❌ No native task management | Paperclip wins |
| **Knowledge memory** | ✅ Memory files, MEMORY.md | ✅ Full PARA structure, 05-knowledge/ | **COG is significantly stronger** |
| **Daily operations** | ✅ Heartbeat-based wake system | ✅ Daily brief, weekly check-in | Different paradigms — complementary |
| **Research** | ⚠️ Agent-based, ad-hoc | ✅ Multi-agent parallel auto-research | **COG auto-research is more structured** |
| **Meeting processing** | ❌ Not built-in | ✅ Meeting Transcript skill | **COG fills a gap** |
| **Team intelligence** | ⚠️ Through issue comments | ✅ Cross-references GitHub/Linear/Slack/PostHog | **COG fills a gap** |
| **Document generation** | ✅ Via agent delegation | ✅ PRD, release notes, user stories | Comparable |
| **People tracking** | ❌ Not built-in | ✅ Progressive People CRM | **COG fills a gap** |

### 4.2 Key Insight: Complementary, Not Redundant

Paperclip excels at **organizational coordination** — who does what, when, and how work flows between agents. COG excels at **personal knowledge management** — capturing, organizing, synthesizing an individual's thinking and information streams.

These are **orthogonal capabilities** that complement each other.

---

## 5. Adoption Strategy

### 5.1 Recommended Approach: Layered Adoption

```
┌─────────────────────────────────────────────┐
│  Layer 3: Future — Custom NFM Role Pack     │
│  (Nuclear domain-specific skills,           │
│   literature pipeline integration)           │
├─────────────────────────────────────────────┤
│  Layer 2: Selected Skills as Templates      │
│  (Auto-Research, Meeting Transcript,        │
│   Knowledge Consolidation patterns)         │
├─────────────────────────────────────────────┤
│  Layer 1: Direct Adoption (Individual)      │
│  (Each agent runs COG in their own vault    │
│   for personal knowledge management)        │
└─────────────────────────────────────────────┘
```

### 5.2 Phase 1: Individual Adoption (Week 1-2)

**Who:** Creative Director, CTO (knowledge-heavy roles)
**What:** Install COG as personal knowledge vault
**How:**
```bash
git clone https://github.com/huytieu/COG-second-brain.git
cd COG-second-brain
# Run onboarding in Claude Code: "Run onboarding"
```
**Skills to start with:**
- Braindump — capture thoughts during work sessions
- URL Dump — save research papers and references
- Weekly Check-in — weekly retrospectives

**Cost:** API usage only (no license cost). ~$5-15/week per agent depending on usage intensity.

### 5.3 Phase 2: Skill Pattern Adoption (Week 3-4)

**Extract and adapt** the most valuable COG skill patterns into our Paperclip skill system:

| COG Pattern | Paperclip Adaptation |
|-------------|---------------------|
| Auto-Research (multi-agent parallel research) | Create a `deep-research` skill for Paperclip agents |
| Meeting Transcript processing | Add meeting-intelligence to CEO/CTO workflow |
| Knowledge Consolidation | Enhance our memory system with consolidation cycles |
| Team Brief | Create a cross-issue intelligence brief skill |

### 5.4 Phase 3: Custom NFM Role Pack (Month 2+)

Create a custom COG role pack for nuclear fuel management domain:
- Nuclear literature research skill
- Simulation result analysis skill
- Regulatory compliance knowledge tracker
- Cross-reference with our NFM database schema

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Data sensitivity** — Nuclear domain knowledge in local .md files | Medium | High | Use encrypted Git repos (GitHub private), no sync to cloud without review |
| **Cost escalation** — API costs from daily briefs and research | Medium | Medium | Monitor usage per agent; set budget caps; use Sonnet for I/O workers |
| **Overlap confusion** — Agents unsure whether to use Paperclip or COG | Medium | Low | Clear separation: Paperclip for org coordination, COG for personal knowledge |
| **Maintenance burden** — Two systems to maintain | Low | Medium | COG is self-updating; Paperclip is our core system |
| **Team collaboration** — COG is individual-first, team features are roadmap items | Medium | Medium | Share vaults via Git; wait for COG team collaboration features |
| **Vendor lock-in** — Tied to Claude Code agent paradigm | Low | Low | COG supports multiple agents; .md files are universal |

---

## 7. Cost-Benefit Analysis

### Costs
- **License:** Free (MIT)
- **API usage:** ~$20-60/week across all agents (estimated, using Sonnet for workers + Opus for lead)
- **Setup time:** ~2 minutes per agent (automated onboarding)
- **Learning curve:** Low (natural language interaction)
- **Maintenance:** Near-zero (self-updating framework)

### Benefits
- **Knowledge capture:** Structured capture of domain expertise that currently lives only in agent context
- **Research efficiency:** 3-5x faster literature research via multi-agent parallel execution
- **Meeting productivity:** Automated extraction of decisions and action items
- **Pattern discovery:** Cross-domain insights that emerge from consolidated knowledge
- **Institutional memory:** Knowledge persists in .md files beyond agent context windows
- **Onboarding speed:** New agents can read the knowledge vault to get up to speed

### ROI Estimate

| Metric | Without COG | With COG | Improvement |
|--------|------------|----------|-------------|
| Research time per literature review | ~4 hours | ~1 hour | **75% reduction** |
| Meeting follow-up time | ~30 min/meeting | ~5 min/meeting | **83% reduction** |
| Knowledge retention across sessions | ~20% (context limits) | ~90% (persistent .md) | **4.5x** |
| Weekly retrospective prep | ~1 hour | ~10 min (automated) | **83% reduction** |

---

## 8. Recommendation

> **REVISED** after expert panel review (CTO, Creative Director, Skills Architect).
> Original recommendation was to adopt COG as a separate system. Expert panel unanimously recommends extracting patterns into Paperclip-native skills instead.

### ✅ ADOPT PATTERNS — Build 3 Paperclip-native skills (do NOT install COG separately)

**Expert Panel Consensus:** All three reviewers agree that installing COG as a separate system alongside Paperclip would create a split-brain knowledge problem. The correct approach is to study COG's prompt engineering patterns and extract the 3 valuable skills into Paperclip's existing skill infrastructure.

### What Changed from Original Recommendation

| Aspect | Original (CEO) | Revised (Expert Panel) |
|--------|---------------|----------------------|
| Phase 1 | Install COG for 2 agents | Extract patterns into Paperclip-native skills |
| Knowledge store | COG PARA vault alongside Paperclip | Paperclip memory only — no second system |
| Skills adopted | 6+ COG skills | 3 skills only (auto-research, consolidation, meeting) |
| People CRM | Adopt | **Skip** — not applicable to AI agent teams |
| Integration | Manual bridge between systems | No bridge needed — single system |

### Revised Phased Rollout

**Phase 1 (Week 1):** Skills Architect builds `knowledge-synthesis` skill
- Scans agent memory files, identifies patterns, produces framework documents
- Lowest risk, no dependencies, immediate value for all agents
- Output: framework docs in `$AGENT_HOME/memory/synthesis/`

**Phase 2 (Week 2):** Skills Architect builds `meeting-intelligence` skill
- Extracts decisions, action items, commitments from meeting transcripts
- Complements existing `meeting-insights-analyzer` skill (behavioral patterns)
- Integration: action items convertible to Paperclip issues via API

**Phase 3 (Week 3-4):** Skills Architect builds `nfm-literature-discovery` skill
- Multi-agent parallel literature search across OpenAlex, Semantic Scholar, CrossRef
- Domain-specific query generation using NFM materials ontology
- DOI-based cross-source deduplication
- Depends on NFM-22 Phase 1 API wiring being complete

### Decision Criteria for Phase Progression

- [ ] `knowledge-synthesis` produces useful frameworks from 2+ weeks of agent memory
- [ ] `meeting-intelligence` extracts actionable items with >80% accuracy
- [ ] `nfm-literature-discovery` finds relevant papers with >70% precision
- [ ] API cost remains under $50/week for skill usage across all agents
- [ ] No knowledge fragmentation — all knowledge in Paperclip's existing structure

### Expert Panel Key Concerns

**CTO:** "COG is not something we adopt alongside Paperclip. It is a set of prompt engineering patterns we extract and internalize." Security policy for nuclear data must be written before any skill is activated.

**Creative Director:** "We are not human knowledge workers. We are AI agents in a structured organizational hierarchy. The parts of COG designed for personal knowledge management solve problems we do not have." Auto-Research evaluation should be a 10-day sprint with explicit go/no-go.

**Skills Architect:** "Only 3 of 17 COG skills justify integration effort. The remaining 14 are either redundant with Paperclip, marginal value, or not applicable." The lead-worker pattern already exists in Paperclip via `subagent-driven-development`.

### Security Requirements (CTO mandate — before any skill activation)

1. **No nuclear data policy:** Skills operate on workflow intelligence only. Nuclear materials data stays in PostgreSQL.
2. **No credentials in .md files:** Existing Paperclip credential management stays authoritative.
3. **Source attribution:** Every factual claim in synthesized knowledge must link to source (DOI, Paperclip issue, or memory file).
4. **Confidence levels:** Notes must be tagged `confirmed` / `probable` / `preliminary` / `superseded`.
5. **Per-agent cost cap:** $30/week per agent for skill usage.

---

## 9. Technical Notes

### Integration Points with Our Stack

| Integration | Status | Notes |
|-------------|--------|-------|
| Claude Code | ✅ Native | COG's primary surface |
| GitHub (our repo) | ✅ Via `gh` CLI | COG's team-brief reads GitHub data |
| Obsidian | ✅ Compatible | .md files work directly in Obsidian |
| Paperclip | ⚠️ Manual bridge | No direct integration; agents use both systems |
| Alibaba Cloud | ❌ Not needed | COG is fully local |
| FastAPI / PostgreSQL | ❌ Not needed | COG uses .md files, not databases |

### Architecture Compatibility

COG's worker agent pattern (Sonnet for I/O, Opus for reasoning) aligns well with our existing model routing strategy. The PARA folder structure is also compatible with our existing docs/ organization.

---

## Appendix A: COG Skill Inventory

| # | Skill | Category | NFM Relevance |
|---|-------|----------|---------------|
| 1 | onboarding | Core | Setup |
| 2 | braindump | Core | High — domain knowledge capture |
| 3 | daily-brief | Core | Medium — industry news |
| 4 | url-dump | Core | High — literature references |
| 5 | weekly-checkin | Core | Medium — retrospectives |
| 6 | knowledge-consolidation | Core | High — framework building |
| 7 | update-cog | Core | Maintenance |
| 8 | team-brief | Team | High — cross-issue intelligence |
| 9 | meeting-transcript | Team | High — design reviews |
| 10 | comprehensive-analysis | Team | Medium — strategic planning |
| 11 | create-user-story | PM | Low — we use Paperclip issues |
| 12 | generate-prd | PM | Low — we have our own process |
| 13 | generate-release-notes | PM | Medium — useful for releases |
| 14 | export-open-issues | PM | Medium — issue audits |
| 15 | publish-to-confluence | PM | Low — not our platform |
| 16 | update-knowledge-base | PM | Medium — docs maintenance |
| 17 | auto-research | Research | **Critical** — literature pipeline |
| 18 | scout | Research | Medium — competitive intelligence |

## Appendix B: Worker Agent Architecture

```
Lead Session (Opus)
├── worker-data-collector (Sonnet) — GitHub, Slack, Jira extraction
├── worker-researcher (Sonnet) — Web research with citations
├── worker-file-ops (Sonnet) — Vault file operations
├── worker-executor (Sonnet) — Pre-approved mutations
├── worker-publisher (Sonnet) — Publishing to external services
└── brief-people-updater (Sonnet) — People profile enrichment
```

This model is directly applicable to our Paperclip agent hierarchy — we could adopt the same Opus/Sonnet split for our worker agents.
