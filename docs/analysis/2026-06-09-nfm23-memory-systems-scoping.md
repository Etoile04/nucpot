# NFM-23: Memory Systems Scoping Analysis for Paperclip

**Date:** 2026-06-09
**Author:** CEO Agent, synthesized from Skills Architect research
**Scope:** 9 memory/AI systems evaluated for Paperclip integration
**Deliverable:** Scoping analysis + ideal memory system blueprint

---

## Executive Summary

Nine memory and AI systems were evaluated for integration into Paperclip's agent orchestration platform. After parallel deep-dive research by the Skills Architect, the findings converge on a clear recommendation:

**Adopt 2 systems (Honcho + MemPalace), extract patterns from 5 others, skip 2 entirely.**

The ideal Paperclip memory system is a **three-layer architecture**: verbatim archive → reasoning/extraction → synthesis/query, built on PostgreSQL (already in our stack).

---

## 1. Evaluation Matrix — All 9 Systems

| # | System | Verdict | Integration Effort | Key Contribution |
|---|--------|---------|-------------------|------------------|
| 1 | **gbrain** | Extract Patterns | High (TypeScript) | Synthesis + gap analysis; self-wiring knowledge graph (zero LLM cost); dream cycle |
| 2 | **COG-second-brain** | Extract Patterns | Low | Knowledge consolidation cycle; citation enforcement; progressive enrichment tiers |
| 3 | **cognee** | Adopt Candidate | Medium | Python-native three-store engine (graph + vector + relational); 14 retrieval modes; Claude Code plugin |
| 4 | **agno** | **Skip** | N/A | Competing agent platform, not a memory system. Weak knowledge capabilities (vector RAG only) |
| 5 | **supermemory** | Extract Patterns | Low (cloud) | Automatic fact extraction; contradiction resolution; temporal forgetting. Cloud-only = risk |
| 6 | **honcho** | **Adopt** | Medium | Peer-centric (observer, observed) model; reasoning pipeline; self-hosted; Python/FastAPI |
| 7 | **mempalace** | **Adopt** (complementary) | Low-Medium | Verbatim archive with 96.6% R@5; MIT license; local-first; pgvector compatible |
| 8 | **memU** | Extract Patterns | High | Hierarchical Resource/Item/Category model; dual retrieval (RAG vs LLM) |
| 9 | **hindsight** | Extract Patterns | Medium-High | Biomimetic memory taxonomy: World/Experiences/Mental Models; 4-way parallel recall |

---

## 2. Detailed Findings

### 2.1 Systems Recommended for Adoption

#### Honcho (plastic-labs/honcho) — Primary Memory & Reasoning Layer

| Aspect | Details |
|--------|---------|
| **Architecture** | Python/FastAPI server + PostgreSQL + pgvector + async reasoning pipeline ("deriver") |
| **License** | AGPL-3.0 (self-hostable, full source) |
| **Why Adopt** | The **peer-centric model** uniquely fits Paperclip's multi-agent hierarchy. The `(observer, observed)` pair mechanism lets each agent model what it knows about every other agent and human user. No other system evaluated has native multi-agent awareness. |
| **Key Features** | Background reasoning deriver, dialectic reasoning levels (minimal→max), representations (low-latency snapshots), chat endpoint for NL queries about peers, session context (token-limited prompt bundles), dream consolidation |
| **Tech Stack Match** | Python + FastAPI + PostgreSQL = exact match with Paperclip |

**Paperclip Integration Map:**
```
Honcho Workspace = Paperclip Company Instance
  Peers = Paperclip Agents (CEO, CTO, CMO, UX, CD) + Human Users
  Sessions = Task execution contexts (issue assignments)
  Conclusions = Auto-extracted knowledge (replaces manual MEMORY.md entries)
  Representations = Agent profile snapshots for context injection
```

#### MemPalace (MemPalace/mempalace) — Verbatim Conversation Archive

| Aspect | Details |
|--------|---------|
| **Architecture** | Local-first CLI + MCP server (29 tools); pluggable vector backend (ChromaDB/Qdrant/pgvector) |
| **License** | MIT (most permissive) |
| **Why Adopt** | Fills the specific niche of **verbatim conversation archival with high-quality semantic retrieval**. 96.6% R@5 with zero API calls. Does not replace reasoning — provides the raw material reasoning operates on. |
| **Key Features** | Spatial metaphor (wings/rooms/drawers), temporal knowledge graph with validity windows, agent diaries, auto-save hooks for Claude Code |
| **Tech Stack Match** | Python + pgvector backend option = compatible |

**Paperclip Integration Map:**
```
Palace = Paperclip Company
  Wings = One per agent (CEO wing, CTO wing...) + One per project
  Rooms = Topics within each agent/project
  Drawers = Verbatim conversation chunks
  Diaries = Agent-specific daily logs
  Knowledge Graph = Temporal entity relationships
```

#### Cognee (topoteretes/cognee) — Alternative Adopt Candidate

| Aspect | Details |
|--------|---------|
| **Architecture** | Python SDK; three-store (graph + vector + relational); 14 retrieval modes |
| **License** | Apache 2.0 |
| **Why Consider** | Most architecturally sophisticated memory engine. Python-native. Claude Code plugin exists. Custom ontology support for nuclear domain. |
| **Concern** | Overlaps significantly with Honcho + MemPalace combined. LLM cost on every ingestion for graph extraction. Recommended as **fallback** if Honcho integration proves difficult. |

### 2.2 Systems Recommended for Pattern Extraction

#### gbrain — Patterns to Extract

1. **Synthesis + gap analysis**: The `think` command produces cited answers with explicit "what we don't know" annotations. Paperclip agents should produce synthesized answers, not just return ranked chunks.
2. **Self-wiring knowledge graph**: Entity extraction from wikilinks and typed-link syntax with zero LLM calls. Every agent output automatically wires graph edges.
3. **Dream cycle**: Scheduled overnight enrichment that deduplicates, fixes citations, scores salience, and finds contradictions.
4. **Schema packs**: Pluggable taxonomy that agents can evolve over time.

#### COG-second-brain — Patterns to Extract

1. **Knowledge consolidation cycle**: Daily capture → weekly pattern analysis → monthly framework synthesis. Explicit cadence for promoting raw data into structured knowledge.
2. **Citation enforcement**: Mandatory `[Source: [[path]] | YYYY-MM-DD | confidence: high|medium|low]` on all durable knowledge writes.
3. **Progressive enrichment tiers**: Entities auto-promote at observation thresholds (1→stub, 3→moderate, 8→full profile).

#### Supermemory — Patterns to Extract

1. **Automatic fact extraction pipeline**: Extract facts from conversations without manual tagging.
2. **Contradiction resolution**: Detect and resolve conflicting facts with temporal awareness.
3. **Static/dynamic profile split**: Long-term identity facts vs. recent activity context, retrieved at ~50ms.

#### memU — Patterns to Extract

1. **Hierarchical Resource/Item/Category model**: Raw data → extracted facts → auto-organized topics.
2. **Dual retrieval modes**: Fast embedding RAG for common queries, deep LLM reasoning for complex ones.

#### Hindsight — Patterns to Extract

1. **Biomimetic memory taxonomy**: Tag all memories as `world` (facts), `experience` (what happened), or `model` (synthesized understanding). This is the single most valuable conceptual contribution.
2. **4-way parallel recall**: Semantic + keyword + graph + temporal, merged via reciprocal rank fusion.
3. **Temporal metadata**: Validity windows on facts — when they become true and when they expire.

### 2.3 Systems to Skip

#### Agno (agno-agi/agno)
Agno is a complete agent platform SDK (50+ API endpoints, AgentOS runtime, Teams, Workflows). It competes with Paperclip's orchestration layer rather than complementing it. Its memory/knowledge capabilities are the weakest of all 9 systems — vector-backed RAG only, no graph, no synthesis, no self-improvement. **Not a memory system — a competing platform.**

---

## 3. Blueprint: Ideal Paperclip Memory System

### 3.1 Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 3: Synthesis & Query                                      │
│ ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│ │ NL Chat Query │  │ Gap Analysis │  │ Cross-Agent Awareness │  │
│ │ (from Honcho) │  │ (from gbrain)│  │ (peer-pair model)     │  │
│ └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│        └────────────┬─────┘                      │              │
└─────────────────────┼────────────────────────────┼──────────────┘
                      │                            │
┌─────────────────────┼────────────────────────────┼──────────────┐
│ Layer 2: Reasoning & Extraction (Honcho)                        │
│ ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│ │ Deriver      │  │ Dream Cycle  │  │ Progressive Enrichment │  │
│ │ (background) │  │ (scheduled)  │  │ (from COG tiers)       │  │
│ └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│        └────────────┬─────┘                      │              │
│  ┌─────────────────┴─────────────────────────────┴───────────┐  │
│  │ Conclusions DB (PostgreSQL + pgvector)                     │  │
│  │ - Extracted facts with confidence levels                  │  │
│  │ - Agent representations (peer-pair scoped)                │  │
│  │ - Temporal validity windows (from Hindsight)              │  │
│  │ - Memory type tags: world / experience / model            │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────────────────┐
│ Layer 1: Verbatim Archive (MemPalace)                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Agent Wings + Project Wings (pgvector)                    │  │
│  │ - Full conversation history, verbatim                     │  │
│  │ - Semantic search (96.6% R@5, zero LLM cost)             │  │
│  │ - Temporal knowledge graph with validity windows          │  │
│  │ - Agent diaries (daily logs)                              │  │
│  │ - No information loss — original always retrievable       │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Memory Type Taxonomy (from Hindsight)

Every memory item is tagged with one of three types:

| Type | Description | Example | Retention |
|------|-------------|---------|-----------|
| **World** | Objective facts about the domain | "NucPot site monitoring runs on GitHub Actions" | Permanent, expires when superseded |
| **Experience** | What happened when we did something | "NFM-18 health check failed 3 times before we fixed the YAML" | Permanent, with timestamp |
| **Model** | Synthesized understanding | "Our team works best with 2-week sprint cycles" | Evolves over time via consolidation |

### 3.3 Citation & Confidence Protocol (from COG)

All durable knowledge writes must include:
```
[Source: [[memory/path]] | 2026-06-09 | confidence: high | type: world]
```

Confidence levels:
- **high** — Confirmed by multiple sources or direct observation
- **medium** — Single reliable source
- **low** — Preliminary, unverified, or heuristic

### 3.4 Progressive Enrichment Tiers (from COG)

Knowledge graph entities auto-promote:
- **Tier 3 (Stub)**: 1 mention → name, type, one-line context
- **Tier 2 (Moderate)**: 3+ mentions → expanded profile, relationships, working patterns
- **Tier 1 (Full)**: 8+ mentions → complete entity with all sections, high confidence

### 3.5 Consolidation Cycle (from COG + gbrain)

| Cadence | Operation | Source Pattern |
|---------|-----------|---------------|
| **Per-session** | Auto-extract facts from agent interactions | Honcho deriver |
| **Daily** | Scan for contradictions, update representations | gbrain signal detector |
| **Weekly** | Pattern analysis across all agent memories; build synthesis docs | COG weekly check-in |
| **Monthly** | Full knowledge consolidation; promote raw captures → frameworks; prune stale entries | gbrain dream cycle |

### 3.6 Cross-Agent Knowledge Sharing

Using Honcho's peer-pair model:

```
CEO knows-about CTO    →  "CTO prefers extract-patterns over install-wholesale"
CTO knows-about CEO   →  "CEO delegates research tasks, expects thorough analysis"
CD knows-about CTO    →  "CTO's architecture reviews are detailed but slow"
CEO knows-about User  →  "User values expert consultation before decisions"
```

Each agent can query: "What do I know about [peer]?" and get a representation built from accumulated observations.

### 3.7 Technology Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| **Verbatim Archive** | MemPalace + pgvector | Local-first, MIT license |
| **Reasoning Engine** | Honcho + PostgreSQL + pgvector | Self-hosted, AGPL-3.0 |
| **Knowledge Graph** | Honcho internal + MemPalace temporal KG | Combined from both systems |
| **Embeddings** | Local models (MemPalace's embeddinggemma-300m) | Zero API cost for retrieval |
| **LLM for Reasoning** | Claude (existing) | Only for extraction/deriver |
| **Storage** | PostgreSQL (existing Paperclip DB) | No new database needed |
| **Agent Interface** | MCP tools (both Honcho and MemPalace provide MCP servers) | Native Claude Code integration |

### 3.8 Cost Estimate

| Component | Weekly Cost | Notes |
|-----------|-----------|-------|
| MemPalace retrieval | $0 | Local embeddings, zero API calls |
| Honcho deriver (extraction) | $10-20/week | LLM calls on ingestion only |
| Consolidation cycle | $5-10/week | Weekly synthesis + monthly deep consolidation |
| **Total** | **$15-30/week** | Within CTO's $30/week per-agent cap |

---

## 4. Phased Implementation Plan

### Phase 1: Foundation (Week 1-2)
- Deploy Honcho server + PostgreSQL + pgvector
- Deploy MemPalace with pgvector backend
- Map Paperclip agents as Honcho peers
- Create agent Wings in MemPalace
- Wire MCP servers into Claude Code config

### Phase 2: Pattern Adoption (Week 3-4)
- Implement memory type taxonomy (world/experience/model)
- Add citation enforcement to `para-memory-files` skill
- Build `knowledge-synthesis` skill (weekly consolidation)
- Implement progressive enrichment tiers on knowledge graph entities

### Phase 3: Advanced Features (Month 2+)
- Build gap analysis layer (from gbrain patterns)
- Implement 4-way parallel recall (from Hindsight patterns)
- Build `nfm-literature-discovery` skill (from COG + cognee patterns)
- Add contradiction resolution (from supermemory patterns)
- Custom deriver logic for nuclear domain extraction

### Phase 4: Optimization (Month 3+)
- Dream cycle (overnight enrichment)
- Cross-agent awareness dashboards
- Knowledge graph visualization
- Self-evolving schema packs

---

## 5. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| **Infrastructure complexity** (Honcho server + MemPalace + pgvector) | Both have Docker Compose setups; pgvector is an extension on existing PostgreSQL |
| **AGPL-3.0 license on Honcho** | Fine for internal use; modifications must be shared if distributed |
| **LLM cost on extraction** | Deriver runs asynchronously, not blocking agent work; cost capped at $30/week/agent |
| **Data migration** from existing MEMORY.md | Phased: run both systems in parallel during Phase 1, migrate in Phase 2 |
| **Nuclear data sensitivity** | Honcho/MemPalace are self-hosted; no external data flow; existing security policies apply |
| **Learning curve** | MCP server integration is turnkey; agents interact via natural language queries |

---

## 6. Decision Criteria for Phase Progression

- [ ] Phase 1: All 5 agents mapped as Honcho peers; MemPalace ingesting conversation history
- [ ] Phase 2: Memory type tags applied to 50+ memory entries; knowledge-synthesis skill producing useful frameworks
- [ ] Phase 3: Gap analysis surface at least 3 "unknowns" per week; literature discovery finds relevant papers with >70% precision
- [ ] Phase 4: Dream cycle reduces manual memory maintenance by >50%
- [ ] Ongoing: API cost under $30/week per agent; no data sensitivity incidents
