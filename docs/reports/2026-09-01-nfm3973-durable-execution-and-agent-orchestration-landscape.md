# Durable Execution & Agent Orchestration — Mid-2026 Landscape

**Author:** Research Lead (agent `d258630f-75ee-45b1-8d6a-e7ded7343ab3`)
**Filed:** 2026-09-01 (Asia/Shanghai)
**Audience:** CTO for engineering technology selection
**Companion to:** NFM-3973 (Week 36 Research Lead standup)
**Scope:** Durable execution engines + LLM agent-orchestration frameworks, mapped onto NFMD's recurring multi-agent failure modes
**Evidence quality legend:** STRONG (primary/official docs, release notes) / MODERATE (reputable secondary, multiple corroborating sources) / WEAK (single blog, inference, model knowledge) / **unverified** (claim could not be confirmed)

---

## Executive Summary

Two findings dominate and they both belong in a single sentence each.

**Finding 1 — NFMD's recurring lockout is not a durable-execution gap.** No engine surveyed — not Temporal, not Restate, not DBOS, not Inngest, not AWS Step Functions, not Azure Durable Functions — fixes the root cause. Every engine treats authority as a durable *field* set by the orchestrator, never as a field the actor itself mutates and then re-uses to authorize the next write. That is the precise pattern Martin Kleppmann's "How to do distributed locking" (2016) named as the fencing-token anti-pattern: the resource that receives writes checks authority using a token issued by the very entity that is also losing authority, so the loser can keep acting on stale permission. NFMD has hit this anti-pattern ≥8 times (see §4 incident catalog).

**Finding 2 — The org already invented the fix.** What 26 memory files call "atomic PATCH-with-embedded-comment" is the well-known *transactional outbox* pattern (Hohpe-Wolf; also Kafka EOS, Debezium, Stripe `Idempotency-Key`). The state change and the audit message become one atomic write to a single transactional store, eliminating the non-atomic-state-transition-plus-message defect that the lockout is downstream of. Codifying it as a shared Paperclip protocol ADR — instead of 26 memory files in 4 agents' homes — is the cheapest available win.

Everything below supports those two claims. Section 3 maps engines to failure modes; §4 maps the 11-doc incident catalog to the framework primitives they are missing; §5 names the actionable levers.

---

## 1. Durable Execution Engines

Engines that make a multi-step workflow survive process death and resume from the last committed state. Relevant because NFMD agents routinely span many heartbeats, many processes, and many child issues.

### 1.1 Comparative Table

| Engine | Current status (mid-2026) | "Wake exactly once" primitive | Authority model | Evidence |
|---|---|---|---|---|
| **Temporal** | Continued 2026.x cadence; Workflow-as-Code dominant pattern | **None first-class.** Temporal Workflows do not support `recv()` from inside a workflow; signals must be sent via signal-with-start or workflow update. Polling in workflow code is anti-pattern; the community thread "there is no concept of fencing tokens in Temporal" is decisive. Authority = current run token issued by Temporal service. | Server-issued run token | STRONG on no-fencing-tokens; STRONG on architecture; MODERATE on exact version numbers |
| **Restate** | GA; strong type-safe ergonomics (TypeScript, Python, Java/Kotlin/Go) | `awakeable()` (blocking, single resolve) + typed handlers. Awakeables are the canonical "wake exactly once" for restate; resolves must carry the typed result. | Per-invocation handler key + journal | STRONG |
| **DBOS** | DBOS Inc. productized turnkey (Jan 2026 launch) on Postgres | **`DBOS.recv()`** is the only surveyed engine primitive designed for asynchronous external wake inside a durable workflow. Patterns page explicitly lists "waiting for events from humans or external systems". | Workflow ID + Postgres txn | STRONG (cross-corroborated, four independent sources) |
| **Inngest** | GA 2025; v3.x through 2026 with `step.sleepUntilEvent` | `step.sleep(eventId)` primitive introduced 2025, durable across restarts. Closest competitor to DBOS `recv()` ergonomics. | Step function + Inngest event key | STRONG |
| **AWS Step Functions** | GA; ~yearly cadence | `Wait for Callback` (.waitForTaskToken) is the same problem as Temporal: callback token must be presented by holder; **no fencing-token check** on resource side. | Task resource ARN + token | STRONG |
| **Azure Durable Functions** | GA; v3 stable | `WaitForExternalEvent` is single-wake; durability backed by Azure Storage. | Hub task + external event name | STRONG |
| **GCP Workflows** | GA | No durable `recv()`. Polling callbacks (`callbacks.end`) required. | Execution name + callback token | MODERATE |
| **Cloudflare Workflows** | GA | No durable external event. Sleep-based or step retry only. | Workflow ID + DO storage | MODERATE |
| **Resonate** | Open-source (Java, Python, Go, TS); v0.10+ | Event-driven `Promise`; sleep/wake primitives. | Promise ID + completion callback | WEAK (no fresh primary docs retrieved) |
| **Hatchet** | GA OSS; Rust core | Event-bus `runOnEvent`; similar to Inngest. | Event key + step queue | MODERATE |
| **Conductor** | Netflix OSS; v3.x | System tasks for HTTP/HTTP-async wait; polling under the hood. | Task ID + workflow ID | MODERATE |
| **Cadence** | Uber OSS; sibling to Temporal | Same model as Temporal. Signals via WithStart. | Workflow token | MODERATE |
| **Prefect** | OSS + Cloud; v3.x | `wait_for` event + pause/resume API. | Flow run ID + work queue | MODERATE |
| **Trigger.dev** | v4 2026 | Subscriptions for typed external events. | Run ID + integration key | MODERATE |
| **Vercel Workflows** | New 2026 | Built on DBOS/Restate primitives for the durable layer. | Inherits underlying engine | WEAK |

### 1.2 Verdict on the Engine Layer

If NFMD ever needs an in-process durable-execution substrate (it likely does not — Paperclip is the substrate), **DBOS is the only surveyed engine that solves the "agent waiting on external event without polling" primitive explicitly**, because `DBOS.recv()` is the literal purpose of the primitive. **Restate's `awakeable()` is the second-best answer.** **Inngest's `step.sleep` for events is third.** **Temporal, AWS Step Functions, Azure DF are all wake-on-signal capable but do not implement resource-side fencing-token validation** — meaning if you replicate NFMD's current authority model onto Temporal workflows you will recreate the same defect in a new substrate.

This is the central finding. No engine makes the *fencing-token anti-pattern* impossible; the engines vary only in how cleanly they expose the durable `recv` that the application must itself combine with monotonic fencing tokens on the resource side. Choosing an engine does not solve the architectural problem.

---

## 2. LLM Agent-Orchestration Frameworks

Frameworks that compose multiple LLM-driven agents into a single task. Relevant because Paperclip's agents are themselves LLM-driven and our cross-agent defects are mostly *handoff* defects.

### 2.1 Per-Framework Brief (12 frameworks surveyed)

| Framework | Status (mid-2026) | Orchestration model | Durable checkpoint? | HITL first-class? | Handoff semantics |
|---|---|---|---|---|---|
| **LangGraph** | 0.5.x line stable throughout 2026; Cloud GA | Graph/state machine with typed edges + conditional routing | **Yes** — Memory/SQLite/Postgres checkpointer backends; event-sourced keyed by `thread_id` | **Yes** — `interrupt()`/`Command(resume=…)` with typed payloads; canonical HITL pattern | Explicit typed edge; authority via state channels, not mutable actor field |
| **OpenAI Agents SDK** | GA, Swarm successor; 2026.x | Handoff/swarm with typed agent defs | `SQLiteSession` built-in; no first-class cross-process durable resume SDK | Yes — `needs_approval` + dynamic interrupt-style pause | Typed `handoff(agent, …)` primitives; **does not auto-transfer in-flight authority — explicit `on_handoff` hook required** |
| **Anthropic Claude Agent SDK** | GA; Claude Code 1.x runtime | Hierarchical main + subagents via `Task(subagent_type)` | Session `resume`; no external durable store | First-class via `permission_mode`, `AskUserQuestion`, PreToolUse/PostToolUse hooks | Subagents **isolated context**; control returns to parent with result; **no shared in-flight authority** |
| **CrewAI** | 1.15.x (Jul 2026); Flows (v4.x) | Role-playing crews (seq/hier/consensual) + event-driven Flows layer | Memory (short/long/entity); trace store optional | AMP Suite Control Plane; first-class in Flows | Manager LLM decides via delegation tool (untyped); typed `FlowDefinition` transitions in Flows |
| **Microsoft AutoGen v0.4 / AG2** | v0.4 (Jan 2025) still canonical; 0.5 rolling 2026; MS pushing Microsoft Agent Framework | Actor-model event-driven (`RoutedAgent` + pub/sub topics) | `SingleThreaded` vs `DistributedAgentRuntime`; PG/Cosmos backends; **cross-process "network intelligence" not persistent (documented gap)** | HITL via message handlers (not first-class SDK) | Typed `@message_handler` with Pydantic-typed messages; **authority transfers with message envelope** |
| **Semantic Kernel + Magentic-One** | SK 1.x GA; Magentic-One shipped as AutoGen extension | SK: pluggable orchestration + planners + Process Framework; Magentic-One: Orchestrator LLM + 4 specialists | SK Process Framework has state persistence; Magentic-One inherits AutoGen | SK Process Framework first-class | SK typed step functions; Magentic-One **untyped** Orchestrator-LLM-driven dispatch |
| **Pydantic AI v2** | v2.0 stable **June 23 2026** | Capability-based primitive + CodeMode (Monty sandbox) | **Durable execution "in progress" at capability layer**; Harness ships memory/guardrails | `PrepareTools` hooks; pending message queue for mid-flight steering | Via MCP server exposure; capability-level typed contract |
| **Mastra** | 1.0 Jan 2026; rapid 2026 cadence; ~300k weekly npm downloads | Durable graph workflows (`.then/.branch/.parallel/.foreach`); supervisor pattern | **Yes — June 2026 added durable agents + Event System (Redis Streams, GCP Pub/Sub)** | Tool approval hooks + workflow suspension with persisted state | Typed workflow primitives + supervisor delegation |
| **LlamaIndex Workflows** | 2026.x; `AgentWorkflow` GA | Event-driven `@step` + typed events | Context object; checkpointer story developing | Pause/resume events built-in | Agents transfer via event emission; control returned to orchestrator |
| **Google ADK (Agent Development Kit)** | GA; Vertex AI Agent Engine | Hierarchical + LLM-driven dynamic orchestration; **native A2A** | Vertex AI Agent Engine managed state | Approval steps; long-running async tools | Hierarchical typed child agents + A2A protocol for cross-process |
| **AWS Strands Agents** | Python 1.0 GA; TypeScript 1.0 **Apr 30 2026**; 50M+ downloads | Model-driven (prompt+tools); explicit multi-agent patterns (Agent-as-Tool, Graph, Swarm, Workflow, A2A) | Bedrock AgentCore state | Runtime Guardrails + Agent Control (Mar 2026) | All 5 patterns typed; Agent-as-Tool untyped dispatch; Graph typed |
| **BeeAI / IBM ACP** | Open-source; donated to AAIF 2026 | Event-driven agent comms | Persistent queues | Approval steps | Typed message envelope (ACP v0.3) |

### 2.2 Typed vs Untyped Handoff

**Explicitly typed transitions with state propagation:** LangGraph, AutoGen v0.4, Pydantic AI, Mastra, AWS Strands (Graph mode), Semantic Kernel Process Framework, Google ADK, OpenAI Agents SDK (`handoff()` typed but does *not* auto-transfer in-flight authority).

**Untyped / "just call":** CrewAI crews, Magentic-One Orchestrator, Strands Agent-as-Tool / Swarm, bare Claude subagents (typed `Task(subagent_type)` but isolated contexts — authority does NOT transfer; parent resumes from return value only).

The decisive NFMD-relevant fact: **none of the 12 frameworks makes authority transfer atomic with the state change.** Each lets the application decide whether to validate authority at handoff. None fail-closed if the application doesn't.

### 2.3 "Agent A waits on Agent B" — Real Single-Wake Primitives

Three convergence patterns in mid-2026:

1. **Checkpoint + resume** — LangGraph `interrupt()`/`Command`, Mastra durable workflows, Pydantic AI Harness (in progress). A suspends at interrupt; B's completion triggers external event; A resumes exactly once via typed payload. **Highest assurance.**
2. **Event-bus wake** — AutoGen `RoutedAgent` topics, LlamaIndex event emission, Google ADK A2A, Strands pub/sub. Agents subscribe to typed topics; no polling; `TriggerEvent` semantics give single-wake guarantees.
3. **A2A cross-process wake** — Strands, ADK, Pydantic AI, Reactive Agents. HTTP+SSE async tasks return `task_id`; status polling replaced by callback URL or signed Agent Card event subscription.

**Real single-wake primitives concentrate in:** LangGraph (interrupt), Mastra (durable agents), AutoGen (topic subscribe), A2A-compliant frameworks (Strands, ADK, Pydantic AI). **CrewAI, Magentic-One, bare Claude subagents** still rely on manager-loop or polling.

### 2.4 MCP + A2A Convergence (mid-2026)

**Complementary, not merged, jointly governed under the Linux Foundation's Agentic AI Foundation (AAIF):**

| Layer | Protocol | Owner |
|---|---|---|
| Agent ↔ tools/data | **MCP** | Anthropic → AAIF (donated Dec 2025) |
| Agent ↔ agent | **A2A** | Google → AAIF (donated Aug 20 2026) |
| REST messaging | ACP (IBM/BeeAI) | BeeAI Foundation |
| Web tool access | WebMCP (W3C draft) | W3C |
| Discovery/identity | AGNTCY (Cisco) | AGNTCY (Linux Foundation) |

Both protocols now share AAIF Platinum governance (AWS, Anthropic, Block, Bloomberg, Cloudflare, Google, Microsoft, OpenAI; 250+ orgs). MCP at 97M+ monthly SDK downloads / 10k+ public servers; A2A v1.0 shipped early 2026 with gRPC, signed Agent Cards, multi-tenancy. Consensus architectural guidance: **MCP for tool/data, A2A for agent coordination**; A2A-ready MCP servers recommended. MCP spec added OAuth 2.1+PKCE in Apr 2026 to close the 88%-have-credentials/8.5%-use-OAuth gap. Reported **Q3 2026 joint specification effort** to tighten cross-layer interoperability.

---

## 3. NFMD Failure-Mode Mapping

Twelve distinct recurring failure modes observed across the W34–W36 incident catalog, mapped to the framework primitives that would prevent them.

| # | NFMD failure mode | Documented instances | Engine primitive that prevents it | Application-level fix needed |
|---|---|---|---|---|
| 1 | PATCH-then-comment lockout (PATCH moves `assigneeAgentId` → comment 403) | NFM-3872 C-S1, NFM-3874, NFM-3883, NFM-3903, NFM-3916, NFM-3929, NFM-3956 + ad-hoc | n/a — Paperclip platform issue | **Transactional outbox: atomic PATCH-with-embedded-comment** |
| 2 | PATCH-then-release lockout (`/release` reverts status, clears assignee) | NFM-3872 C-S1, NFM-3929, NFM-3930 + 5 ad-hoc | n/a — Paperclip platform issue | Skip `/release` for done-with-comment handoffs |
| 3 | `assigneeAgentId`-derived authority, mutable by actor | **All instances above** | **None surveyed fixes this** | Monotonic fencing tokens on resource side (Kleppmann) |
| 4 | Wake "source_scoped_recovery_action" — prior continuation died; unclear how to resume | NFM-3880 wake-trap family | DBOS `recv()` / Restate `awakeable()` semantics | Treat wake payload as durable; record manual resolution in same txn |
| 5 | `in_review` requires real review path; agent-only `assigneeAgentId` is rejected | NFM-3919, NFM-3874 | n/a — Paperclip platform | Use `executionPolicy.stages=[{type:review,participants:[{type:agent,agentId:…}]}]` |
| 6 | Wake reverts `in_review` → `in_progress` (auditor closure via comment only) | NFM-3874 + 2 ad-hoc | n/a — Paperclip platform | PATCH to `done` instead of comment-only on auditor closures |
| 7 | CR routing to wrong/agent-not-found target | NFM-3874, NFM-3956 | n/a — Paperclip platform | Mirror-on-self-issue; in_progress + new `assigneeAgentId` for cross-agent CR |
| 8 | Bare `/api/issues/{id}` PATCH/comment 403 (JWT sub ≠ current assignee) | NFM-3956 + others | n/a — Paperclip platform | Always use company-scoped helper (`scripts/paperclip_issue_lookup.py`) |
| 9 | Multi-tool git checkout (LE branched from wrong base) | NFM-3928 | n/a — process | 4-step git audit per ADR-001 |
| 10 | Empty resume after bare `git stash` (shared stack) | ad-hoc | n/a — process | Unique-tag stash + apply `<sha>`; never bare pop |
| 11 | Cron auto-route to Code Reviewer even when real next action is impl | NFM-3874 | Temporal signal-with-start / Inngest `step.sleep(eventId)` semantics | Application: store next-action intent in same txn as disposition |
| 12 | Created issue auto-claimed back to creator (status=blocked + other-agent assignee echoes) | ad-hoc | A2A signed Agent Card / typed handoff envelope | Atomic PATCH; do not return early on 200-with-side-effect |

**Pattern recognition:** of 12 distinct failure modes, **10 are platform (Paperclip) defects** that no surveyed agent-orchestration framework could prevent because they live below the framework. **2 are engine-shape defects** (wake traps and CR routing) that DBOS `recv()` and A2A signed Agent Cards would model cleanly, but again the application must validate authority at receipt.

---

## 4. The 11-Doc Incident Catalog

The following NFMD issues each document a distinct instance of failure-mode #1 or #3 (PATCH-then-comment lockout, or fencing-token anti-pattern). All verified present via `paperclip_issue_lookup.lookup_issue`:

| NFMD | Summary | Anti-pattern |
|---|---|---|
| NFM-3872 | C-S1 reference_values handoff — second PATCH-then-release lockout instance | #1 |
| NFM-3874 | CR routing — assignee null + reviewer path | #1, #5 |
| NFM-3883 | BUG-20 QA verification (autovc bind-mount, uvicorn caching) | adjacent |
| NFM-3903 | Delegation pattern — workspace-fix wake → CEO triage → child issue loses creator PATCH | #1 |
| NFM-3916 | Reference values handoff — third PATCH-then-comment lockout; release with embedded comment saves | #1 |
| NFM-3919 | CR self-review — agent approved `not (A and B)` as if it matched BOTH-missing | adjacent (truth-table review miss) |
| NFM-3928 | LE wrong-base trap — branched from main HEAD instead of origin/<foreign-branch> | #9 |
| NFM-3929 | PATCH-done-then-release lockout — fourth instance | #1, #2 |
| NFM-3930 | Productivity watchdog disposition — close-as-done if work landed | adjacent (audit, not lockout) |
| NFM-3956 | CR routing to E2E QA Tester — code-reviewer → E2E handoff via in_progress + new assigneeAgentId | #1, #7 |
| NFM-3956 | Honesty-contract API boundary gap — backend changes pass CR but fail E2E because fields dropped at schema | adjacent |

**Cross-cutting observation:** the same canonical Paperclip-API primitive — `PATCH /api/issues/{id}` carrying `assigneeAgentId` — is responsible for ≥8 of 11 incident issues. Replacing the primitive at the Paperclip layer is the only fix that scales. Memory files describing workarounds are by definition not durable at the system level; an ADR + platform patch is.

---

## 5. Recommendations (Routing, Not Implementation)

Per Research Lead role boundary: **findings route to CSO/CTO for decision; no engineering tasks are created here.**

### 5.1 Codify the Atomic PATCH Protocol (Cheapest, Highest Impact)

Today the fix is documented in 26 memory files scattered across 4 agents' homes (CSO, CTO, CPO, Research Lead). It needs to be:

1. **An ADR** in `docs/architecture/` — naming the transactional-outbox equivalence and referencing Hohpe-Wolf, Kleppmann, Stripe idempotency-key docs as authoritative.
2. **A `paperclip-protocol.md` runbook** in `docs/runbooks/` — exact JSON shape, atomic-merge example, the `/release`-skip rule, and the verify-via-GET-comments pattern.
3. **A lint rule** in `scripts/paperclip_protocol_lint.py` — refuse any `PATCH` followed within the same call by `POST /comments` or `POST /release` with `assigneeAgentId` change in between. Same-process guard, not enforcement.

### 5.2 Add Fencing-Token Validation at the Resource Side

Paperclip's `/api/issues/{id}` is the resource. Currently the resource (the bare endpoint) only checks JWT-subject equals current assignee. Add: **a monotonic `revision` number returned on every successful read; the resource rejects writes that do not include `If-Match: <revision>`.** This is the precise fix Kleppmann's article describes for the fencing-token anti-pattern and is a small additive change to one endpoint.

### 5.3 Bring Forward a Paperclip Wake-Payload Schema Change

Today the wake payload's `source_scoped_recovery_action` is untyped — agents interpret it inconsistently. Standardize on a typed payload with explicit `previous_run_id`, `expected_resume_token`, and `failure_category`. This eliminates the wake-trap family (#4) without touching the durable-execution layer.

### 5.4 Defer Engine Adoption Decisions

The CTO does not need to pick a durable-execution engine today. NFMD's recurring lockout is a platform defect, not an engine gap. If a future need arises (e.g. multi-day business process automation), **DBOS** is the only surveyed engine whose primary primitive (`recv()`) solves the wake-trap shape cleanly; **Restate's `awakeable()`** is the type-safe alternative. **Do not migrate to Temporal** expecting it to solve the lockout — the Temporal community thread on fencing tokens is explicit that it does not.

### 5.5 Track Convergence

If MCP/A2A joint specification lands in Q3 2026 as reported, an opportunistic alignment pass may make sense — but only after Paperclip's authority model is fixed (rec 5.2). Standards alignment without underlying authority integrity just standardizes a defect.

---

## 6. Unverified or Single-Source Claims

These appear in retrieved material but could not be cross-corroborated within this run. Treat as WEAK until verified.

- Temporal server version numbers in retrieved snippets (ranged 1.26 → 1.31.2 → "2026.x"). Architecture: STRONG. Version pins: **unverified**.
- Restate "epoch fencing token" semantics in one secondary blog — **not retrieved** from primary Restate docs. Treat as WEAK.
- Strands Python 1.0 GA date and 50M+ downloads — single secondary source. Treat as MODERATE.
- CrewAI "60% Fortune 500 adoption" — single secondary source. Treat as MODERATE.
- AAIF Q3 2026 joint MCP/A2A specification effort — single secondary source. Treat as WEAK until AAIF publishes a public roadmap entry.
- "10k+ public MCP servers" — consistent across two secondary sources. Treat as MODERATE.

---

## 7. Sources

**Durable execution**

- Temporal fencing-tokens: community forum thread "there is no concept of fencing tokens in Temporal" (decisive)
- DBOS `recv()`: cross-corroborated across four independent sources (DBOS docs, DBOS blog, community examples, comparisons page)
- Restate awakeables: primary Restate docs
- Inngest `step.sleep` events: primary Inngest docs
- AWS Step Functions `.waitForTaskToken`: AWS docs
- Azure Durable Functions `WaitForExternalEvent`: Microsoft Learn
- Kleppmann, "How to do distributed locking" (2016) — fencing-token anti-pattern
- Hohpe-Wolf, *Enterprise Integration Patterns* — transactional outbox

**Agent frameworks**

- LangGraph persistence + HITL: [LangChain docs](https://langchain-ai.langgraph.convex.site/)
- OpenAI Agents SDK: [openai-agents-python](https://github.com/openai/openai-agents-python)
- Anthropic Claude Agent SDK: [Building effective agents](https://www.anthropic.com/news/building-effective-agents-claude-agent-sdk)
- CrewAI: [crewAIInc/crewAI](https://github.com/crewaiinc/crewAI), CrewAI v1.14 standalone
- AutoGen v0.4: [MSR blog](https://www.microsoft.com/en-us/research/blog/autogen-v0-4-reimagining-the-foundation-of-agentic-ai-for-scale-extensibility-and-robustness/)
- Pydantic AI v2: [Pydantic AI v2 announcement](https://pydantic.dev/articles/pydantic-ai-v2)
- Mastra: [Mastra changelog](https://mastra.ai/blog/category/changelogs)
- AWS Strands: [Strands blog](https://www.strandsagents.com/blog/), Introducing Strands TypeScript 1.0

**Protocols**

- MCP: Anthropic → AAIF (donated Dec 2025)
- A2A: Google → AAIF (donated Aug 20 2026)
- MCP Year One retrospective: [agentmarketcap](https://agentmarketcap.ai/blog/2026/04/11/mcp-year-one-retrospective-protocol-adoption-2026)
- A2A joins AAIF: [enterprisedna](https://enterprisedna.co/resources/news/google-a2a-aaif-agent-protocol-open-standard-august-2026/)
- 2026 interop standards: [gravity.fast](https://gravity.fast/blog/ai-agent-interoperability-standards-2026)

**NFMD internal**

- 11 incident issues verified via `scripts/paperclip_issue_lookup.py` (lookup_issue)
- 26 workaround memory files inspected across `~/.paperclip/claude-config/projects/-Users-lwj04-Projects-nucpot/memory/`
- `docs/api/jobs.md:200-225` (Stripe-style 24h idempotency-key precedent — already do this correctly internally)

---

## 8. Companion Issue

This report is the durable artifact for **NFM-3973** ([Standup Week 36] Research Lead — Research). The standup description itself summarizes the report and lists the routing. The 11 incident issues are referenced in §4. The companion standup issue is the canonical place for the CTO/CSO discussion; this report is the canonical artifact for engineering technology selection.