# ADR-019 — First-seen mode=mix latency budget: decision record → tiered cold-query contract (NFM-4823)

| Field | Value |
| --- | --- |
| **Status** | Accepted (2026-09-14) — final decision is the [ADR-NFM-3404 §9 tiered cold-query contract](../architecture/ADR-NFM-3404-rag-query-timeout-alignment.md#9-tiered-cold-query-contract-nfm-4823--nfm-4825-2026-09-14); this ADR is the decision record. The initial "re-tier inside the ≤15 s ceiling" draft was superseded the same day, before landing (§2.2). |
| **Date** | 2026-09-14 |
| **Author** | CPO (product decision; SLA re-decision delegated via NFM-4823 assignment) |
| **Scope** | Prod/staging query-latency budget for the RAG flagship path (`mode=mix`, first-seen/uncached); env vars `NFM_LIGHTRAG_QUERY_TIMEOUT_S`, `LIGHTRAG_TIMEOUT` (sidecar upstream), `NFM_LIGHTRAG_QUERY_CONNECT_S`, `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS` |
| **Amends** | [ADR-NFM-3404](../architecture/ADR-NFM-3404-rag-query-timeout-alignment.md) — §9 (added there, via NFM-4825) supersedes §8's `mix` no-cache row ("2–6 s", cache-hit-masked) and §2.1's single ≤15 s algebra for the query path |
| **Implemented by** | [NFM-4825](/NFM/issues/NFM-4825) → PR #1355 (compose read 22.0, abort 25 000 ms, spec §5 sync, tier-2 target 20 000 ms, this file's ADR-3404 §9 amendment) |
| **See also** | [NFM-4823](/NFM/issues/NFM-4823) (decision ticket; full rationale in its 2026-09-14 comments), [NFM-4824](/NFM/issues/NFM-4824) (measurement: g_p95 ≈ 17.1 s uncontended), [NFM-4822](/NFM/issues/NFM-4822) (rerank fix chain PR #1352/#1353), [NFM-4492](/NFM/issues/NFM-4492) (30 s valve, walked back), [NFM-4521](/NFM/issues/NFM-4521) / [NFM-4525](/NFM/issues/NFM-4525) / NFM-4527 (model swap + `think=False` + baked binding), [NFM-4422](/NFM/issues/NFM-4422) / [NFM-4820](/NFM/issues/NFM-4820) (pre-warm cron pattern) |

---

## 1. Context

### 1.1 What NFM-4822's closeout found (2026-09-13T19:33–19:35Z, prod @ d55a7e7c1)

The rerank regression itself is fixed (PR #1352 + #1353: candidate pool capped
85→24, each pass ~2 s, no worker timeouts). What remains is a **pre-existing,
separate** bottleneck that NFM-4822's 100%-fallback state had been masking:
uncached LLM generation time.

- First-seen variant query 「UO2 热导率 随燃耗深度如何变化」 × 2:
  `was_fallback=t`, `query_kind=ilike`, `time_total` 10.02 s / 10.03 s
  (10.0 s `NFM_LIGHTRAG_QUERY_TIMEOUT_S` + ~2 s ILIKE rescue).
- Sidecar logs show rerank completing cleanly (`Successfully reranked: 6
  chunks from 24 original chunks`) and the query LLM call starting — the
  client timeout fires **mid-generation**. The answer finishes server-side
  and is cached (`== LLM cache == saving: mix:query:cfec…`).
- Immediate-repeat pair: `query_kind=semantic`, 4.65 s / 4.52 s,
  `fallback.used=false` — the repeat-semantic path is healthy.

### 1.2 Why ADR-NFM-3404 §8 did not catch this

The §8 per-mode table predicted `mix` no-cache at **2–6 s**. That number was
never observed cold: even the 2026-09-12T17:00Z pre-degradation semantic
successes (2.3 s) were LLM-cache hits. An uncached semantic mix query has
likely **never** fit the read budget. The prediction was cache-hit-masked.

### 1.3 The product defect in one sentence

First-seen users systematically receive the **worst** answer tier (ILIKE
keyword rescue) while the **best** answer (semantic) completes server-side
seconds later and is cached — worst-answer-first, best-answer-on-repeat is
the exact inverse of the product promise.

### 1.4 Deployed config, reconciled (NFM-4824 Step 0, read from the live stack @ d55a7e7c1)

| Layer | Variable | Deployed value |
| --- | --- | --- |
| L3 backend read | `NFM_LIGHTRAG_QUERY_TIMEOUT_S` | **10.0** (compose pin; no `.env.prod` override) |
| L2 connect | `NFM_LIGHTRAG_QUERY_CONNECT_S` | unset → code default 5.0 |
| L1 sidecar→LLM | `LIGHTRAG_LLM_TIMEOUT_S` / internal | not wired → LightRAG internal default; sidecar arms query/keyword LLM funcs at 240 s; sidecar upstream `LIGHTRAG_TIMEOUT=60.0` |
| L4 frontend abort | `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS` | 15 000 ms (module default, no build override) |
| rerank | `RERANK_TIMEOUT` / `RERANK_POOL_CAP` | 6 / 24 |

### 1.5 The constraint as initially understood

ADR-NFM-3404's layered invariant (§2.1) capped user-visible wait at **≤15 s**,
with the frontend AbortController above the backend timeout — so the initial
decision space was framed as "re-tier inside the envelope". §2.2 records that
draft; §2.1 records why it was superseded within the same decision cycle.

## 2. Decision

### 2.1 Final decision — tiered cold-query latency contract (CPO, NFM-4823 comment 2026-09-14T19:54Z)

**Consciously re-decide the SLA** (NFM-4823 AC1 branch 2): replace the single
≤15 s wall-clock bound for the query path with a **warm/cold tiered contract**,
recorded authoritatively as ADR-NFM-3404 §9:

| Tier | Path | Contract | Backing knob |
| --- | --- | --- | --- |
| **Warm** | LLM-cache hit (repeat / variant-of-seen) | target p95 ≤ 10 s — unchanged (observed 4.5–6.1 s) | n/a |
| **Cold** | first-seen, uncached generation | expected 12–20 s, hard bound 25 s | read 10→**22 s**, abort 15 000→**25 000 ms** |
| **Beyond bound** | generation unfinished at 22 s | ILIKE fallback preserved verbatim (`fallback.used=true, reason=semantic_timeout`, RagFallbackBadge) | fallback contract (NFM-4734) unchanged |

**Why superseding the ≤15 s single bound is correct:** it conflated warm and
cold paths. At the 10 s budget a cold query already cost the user ~11.4 s and
returned the *degraded* ILIKE answer while the semantic answer completed
server-side moments later and was discarded for that request. Raising the read
budget to 22 s converts roughly the same wait into the full semantic answer,
and every subsequent ask of that question lands in the 4.5–6 s warm tier. For
a research tool whose core value is synthesized, honest answers (the
NFM-4539 honesty chain), a bounded +10 s on rare cold queries is the right
trade.

**Sidecar upstream stays at 60 s — load-bearing:** generation continuing
server-side past a client read timeout is what populates the LLM cache; the
warm tier exists because of it. Do not "align" it down.

### 2.2 Superseded same-day draft — "re-tier inside the ≤15 s envelope" (for the record)

The first CPO pass (NFM-4823 comment 2026-09-14T19:53Z, ~90 s earlier)
reaffirmed the ≤15 s ceiling and chose `NFM_LIGHTRAG_QUERY_TIMEOUT_S = 12`
with a measurement gate (g_p95 ≤ 8 s land / 8–9.5 s tune / > 9.5 s STOP) and
"streaming as the P1 structural fix; interim = intentional
fallback-first-seen" if generation could not fit. It was superseded before
landing: the same decision cycle's closeout probes (11.42 s cold fallback vs
6.08 s warm semantic, `tier_2_p95 = 10 011 ms` vs 10 000 target) showed the
envelope framing itself was wrong — the user already pays ~11 s on the cold
path and gets the worst tier for it. NFM-4824's later uncontended measurement
(g = 15.36 / 16.82 / 17.11 s) confirmed the draft would have hit its own STOP
branch: 12 s read + ~7–10 s retrieval/kw/rerank never covers ~17 s generation.

### 2.3 Rejected and deferred options

- **Bounding first-answer length** to fit a ~4 s residual window — flagship
  mix answers are ~1000-char Chinese syntheses; truncation trades the
  product's core value for a latency number. (Token-cap infrastructure from
  NFM-4730-fixB exists if abuse control ever needs it.)
- **Faster query LLM / GPU scheduling** — P2 candidate; revisit only if AC2
  probes show the cold distribution clustered just above the 22 s read.
- **Async/streaming first-seen UX** — the proper structural fix and the named
  P1 follow-up if the tiered contract's AC2 evidence shows first-seen
  fallback share staying high (§2.4); a feature epic, not this fix.
- **Cache seeding of canonical flagship queries** — P2 operational complement
  (extends the NFM-4422 / NFM-4820 pre-warm pattern); converts known cold
  paths to warm ahead of demos.

### 2.4 Residual risk, stated honestly — AC2 adjudicates

NFM-4824's uncontended pre-change probes measure the full server-side cold
wall at **≈ 23.7–27.6 s** (kw ~2.0 s + retrieval ≤ 0.4 s + rerank 4.6–8.5 s +
generation 15.4–17.1 s) — i.e. a material share of cold queries may still
exceed the 22 s read and take the beyond-bound path (ILIKE at ~23 s under the
25 s abort; warm tier on repeat). The contract's claim is that the cold
*distribution* (12–20 s expected) covers the common cases, not all of them.
The post-deploy AC2 probes on NFM-4825 are the adjudication: if first-seen
`was_fallback` share remains high against the ≤20 % / 7-day KPI, the next
step is the streaming P1 — **not** a further synchronous read raise past
25 s.

## 3. Consequences

- Cold-path latency rises (up to ~23 s beyond-bound worst case vs ~11.4 s
  today) but the answer tier upgrades ILIKE→semantic on first contact
  whenever generation fits — which is the product promise.
- **KPI:** first-seen mode=mix `was_fallback=t` share in `rag_access_log` —
  from ~100 % to ≤20 % within 7 days of deploy; adjudicated per §2.4.
- Tier-2 metrics target moves 10 000 → 20 000 ms (`rag_metrics.py`); warm and
  cold currently share one p95 — splitting them is future work (§9.4 of
  ADR-3404).
- Longer per-query LLM occupancy on the shared Metal GPU; watch concurrent
  queueing. The rerank pool cap (PR #1353) keeps rerank ≈ 2 s per pass.
- Touch points, all landed in PR #1355: `docker-compose.prod.yml`,
  `docker-compose.staging.yml`, `.env.lightrag.example` invariant comment,
  `apps/web/src/lib/rag-api.ts` (+ `rag-contract.ts`), `rag_metrics.py`,
  `docs/specs/RAG-anonymous-open-and-quality.md` §5,
  `scripts/check_staging_rag_parity.py` expectations, tier-2 tests.

## 4. Verification (maps to NFM-4823 acceptance criteria)

- **AC1** branch 2 (conscious re-decision, documented, fallback stated): this
  ADR + ADR-NFM-3404 §9 are the record; the beyond-bound ILIKE behavior is
  stated in both. Post-deploy first-seen probes showing semantic answers
  within 22 s would additionally satisfy branch 1 for those queries.
- **AC2:** post-deploy evidence comment on NFM-4823 links `rag_access_log`
  rows for ≥ 3 fresh first-seen variant queries (cold/warm mix) with
  `query_kind`, `was_fallback`, and `time_total` against the new contract;
  the measured distribution is appended to ADR-3404 §9.3.

## 5. References

- NFM-4823 — decision ticket (rationale comments 2026-09-14T19:53Z / 19:54Z)
- NFM-4824 — measurement (config reconciliation + uncontended g probes)
- NFM-4825 — implementation (PR #1355)
- NFM-4822 — rerank degradation fix chain (PR #1352, PR #1353)
- NFM-3403 / NFM-3404 — error contract + timeout alignment (framework)
- NFM-4492 — 30 s valve raised then walked back to 10 s
- NFM-4521 / NFM-4525 / NFM-4527 — qwen3.5:4b-nvfp4 swap, `think=False`,
  baked ollama binding
- NFM-4730-fixB — LLM token-cap infrastructure (cited by §2.3)
- NFM-4422 / NFM-4820 — pre-warm cron precedent (cited by §2.3)
