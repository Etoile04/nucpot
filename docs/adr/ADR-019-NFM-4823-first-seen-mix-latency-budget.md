# ADR-019 — First-seen mode=mix latency budget: semantic-first re-tier inside the ≤15 s user ceiling (NFM-4823)

| Field | Value |
| --- | --- |
| **Status** | Proposed (CPO decision per NFM-4823; awaiting CTO review) |
| **Date** | 2026-09-14 |
| **Author** | CPO (product decision; SLA re-decision delegated via NFM-4823 assignment) |
| **Scope** | Prod/staging query-latency budget for the RAG flagship path (`mode=mix`, first-seen/uncached); env vars `NFM_LIGHTRAG_QUERY_TIMEOUT_S`, `LIGHTRAG_LLM_TIMEOUT_S`, `NFM_LIGHTRAG_QUERY_CONNECT_S`, `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS` |
| **Amends** | [ADR-NFM-3404 §8 per-mode latency budget](../architecture/ADR-NFM-3404-rag-query-timeout-alignment.md) — its `mix` no-cache row ("2–6 s") is empirically wrong on the post-NFM-4822 stack |
| **See also** | [ADR-NFM-3404 — RAG query timeout alignment](../architecture/ADR-NFM-3404-rag-query-timeout-alignment.md), [NFM-4822](/NFM/issues/NFM-4822) (rerank fix chain PR #1352/#1353), [NFM-4823](/NFM/issues/NFM-4823) (this decision's ticket), [NFM-4492](/NFM/issues/NFM-4492) (30 s valve, walked back), [NFM-4521](/NFM/issues/NFM-4521) / [NFM-4525](/NFM/issues/NFM-4525) / NFM-4527 (model swap + `think=False` + baked binding), [NFM-4422](/NFM/issues/NFM-4422) / [NFM-4820](/NFM/issues/NFM-4820) (pre-warm cron pattern) |

---

## 1. Context

### 1.1 What NFM-4822's closeout found (2026-09-13T19:33–19:35Z, prod @ d55a7e7c1)

The rerank regression itself is fixed (PR #1352 + #1353: candidate pool capped
85→24, each pass ~2 s, no worker timeouts). What remains is a **pre-existing,
separate** bottleneck that NFM-4822's 100%-fallback state had been masking:
uncached LLM generation time.

- First-seen variant query 「UO2 热导率 随燃耗深度如何变化」 × 2:
  `was_fallback=t`, `query_kind=ilike`, `time_total` 10.02 s / 10.03 s
  (≈8 s effective `NFM_LIGHTRAG_QUERY_TIMEOUT_S` + ~2 s ILIKE rescue).
- Sidecar logs show rerank completing cleanly (`Successfully reranked: 6
  chunks from 24 original chunks`) and the query LLM call starting — the
  client timeout fires **mid-generation**. The answer finishes server-side
  and is cached (`== LLM cache == saving: mix:query:cfec…`).
- Immediate repeat pair: `query_kind=semantic`, 4.65 s / 4.52 s,
  `fallback.used=false` — the repeat-semantic path is healthy.
- Budget math: retrieval ≈1.5 s + two capped rerank passes ≈2.5 s leaves ~4 s
  for generation of a ~1000-char Chinese answer; qwen3.5:4b-nvfp4 on the
  shared Metal GPU does not fit.

### 1.2 Why ADR-NFM-3404 §8 did not catch this

The §8 per-mode table predicted `mix` no-cache at **2–6 s**. That number was
never observed cold: even the 2026-09-12T17:00Z pre-degradation semantic
successes (2.3 s) were LLM-cache hits. An uncached semantic mix query has
likely **never** fit the budget (the NFM-3404 cold-query budget theme). The
prediction was cache-hit-masked; this ADR corrects the row.

### 1.3 The product defect in one sentence

First-seen users systematically receive the **worst** answer tier (ILIKE
keyword rescue) while the **best** answer (semantic) completes server-side
seconds later and is cached — worst-answer-first, best-answer-on-repeat is
the exact inverse of the product promise.

### 1.4 Config drift that must be reconciled before landing

- `docker-compose.prod.yml:123` declares `NFM_LIGHTRAG_QUERY_TIMEOUT_S:
  "10.0"`, but observed behavior (10.02 s total = timeout + ~2 s rescue)
  implies an **effective ≈8 s** — the deployed env (`docker/.env.prod`,
  container env) must be read before changing anything.
- The sidecar's `LIGHTRAG_LLM_TIMEOUT_S` (default 8) must not abort
  generation before the backend budget expires, or a raised backend timeout
  buys nothing (sidecar 5xx → fallback anyway). Evidence that generation
  outran 8 s server-side without dying means the effective sidecar budget is
  already higher — verify, don't assume.

### 1.5 Constraint that bounds every option

ADR-NFM-3404's layered invariant (§2.1) caps user-visible wait at **≤15 s**,
and the frontend AbortController (14–15 s) sits **above** the backend
timeout. Any backend raise past ~13 s collides with the frontend abort: the
user would never see the answer we waited for. So the decision space is
"re-tier inside the envelope", not "raise the ceiling" — unless the ceiling
itself is consciously re-decided (D4 covers when that becomes necessary).

## 2. Decision

### D1 — Reaffirm the ≤15 s user-visible ceiling

ADR-NFM-3404 AC-1 stands unchanged. No blind timeout raise past the
frontend abort.

### D2 — Re-tier the budget inside the envelope: first-seen semantic gets the budget; ILIKE becomes the intentional below-timeout rescue

Land `NFM_LIGHTRAG_QUERY_TIMEOUT_S = 12` (backend httpx read), keeping the
ADR-NFM-3404 invariant stack HOLDS end-to-end:

```
LIGHTRAG_LLM_TIMEOUT_S (9–10) + NFM_LIGHTRAG_QUERY_CONNECT_S (2–3) + 1_safety
    <= NFM_LIGHTRAG_QUERY_TIMEOUT_S (12)
    <= NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS / 1000 (14–15)
    <= 15                                   # user-visible ceiling (D1)
worst rescue-inclusive path: 12 + ~2 (ILIKE) = 14 <= frontend abort   ✓
```

This buys uncached mix generation ~8 s after ~4 s retrieval+rerank, instead
of today's ~4 s.

### D3 — Measurement-gated landing (Lead Engineer)

Before deploying, measure actual first-seen mix generation duration
server-side — sidecar logs for the 2026-09-13T19:34Z pair (cache key
`mix:query:cfec…`) plus ≥2 fresh first-seen probes (unique variant phrasing,
confirm cache miss). Let `g_p95` = p95 generation duration (LLM call start →
answer complete):

| Measurement | Action |
| --- | --- |
| `g_p95 ≤ 8 s` | Land the D2 stack as specified (12 s read budget) |
| `8 s < g_p95 ≤ 9.5 s` | May tune within the invariant (connect 3→2, read ≤13 s, sidecar LLM budget up) — no re-consult needed |
| `g_p95 > 9.5 s` | **STOP — do not deploy.** Post the numbers on the implementation ticket. First-seen semantic cannot fit ≤15 s; route to D4 |

### D4 — If generation cannot fit: streaming is the P1 structural fix; interim fallback-first-seen becomes intentional

If `g_p95 > 9.5 s`, the correct product answer is **not** a ceiling raise
past 15 s on the synchronous path. Open the async/streaming first-seen UX
option (accept >15 s server-side, surface progress) as a P1
decision-and-implementation ticket. Interim behavior is today's
fallback-first-seen — now **documented as intentional** (AC1 branch 2):
first-seen = ILIKE tier at ≤~10–14 s, repeat = semantic at ~4.6 s via LLM
cache.

### D5 — Rejected: bounding first-answer length to fit the residual window

Flagship mix answers are ~1000-char Chinese syntheses over mixed retrieval;
truncating output to fit a ~4 s generation window trades the product's core
value for a latency number. (Token-cap infrastructure exists from
NFM-4730-fixB if abuse control ever needs it — not for flagship quality.)

### D6 — Deferred (P2, ops): cache pre-warm for canonical flagship queries

Extend the existing pre-warm cron pattern (NFM-4422 / NFM-4820) to issue the
canonical flagship/demo query set ahead of demos. The repeat path is already
healthy (~4.6 s semantic); this only removes first-contact ILIKE for known
queries.

### D7 — Out of scope: faster query LLM / GPU scheduling

Revisit only if D3 measurement lands in the 9.5–12 s band, where small
scheduling wins between ollama (4B) and llama-server (rerank) would flip the
outcome.

## 3. Consequences

- First-seen latency rises modestly (≤12 s semantic vs ~10 s ILIKE today)
  but the answer tier upgrades ILIKE→semantic whenever generation fits.
- **KPI:** first-seen mode=mix `was_fallback=t` share in `rag_access_log` —
  from ~100% to ≤20% within 7 days of deploy (on the D3 pass branch).
- Longer per-query LLM occupancy on the shared Metal GPU; watch concurrent
  queueing. The rerank pool cap (NFM-4822 PR #1353) keeps rerank ≈2 s.
- ADR-NFM-3404 §8 `mix` no-cache row is corrected by this ADR: the 2–6 s
  figure was a cache-hit-masked prediction, never observed cold.
- Touch points that encode the old budget and must stay coherent in the
  implementation PR: `docker-compose.prod.yml`,
  `docker-compose.staging.yml`, `.env.lightrag.example` (keep the invariant
  comment truthful), `docker/lightrag/sitecustomize.py` docstring
  references, `scripts/check_staging_rag_parity.py` (+ its test), and the
  deployed `docker/.env.prod` via the standard deploy process.

## 4. Verification (maps to NFM-4823 acceptance criteria)

- **AC1** is satisfied either by D2/D3 landing (first-seen semantic within
  budget — branch 1) or by this ADR plus D4's documented interim behavior
  (conscious re-decision, fallback stated — branch 2).
- **AC2:** post-deploy evidence comment on NFM-4823 links `rag_access_log`
  rows for ≥2 fresh first-seen variant queries showing `query_kind=semantic`,
  `was_fallback=f`, and `time_total` within the new budget.

## 5. References

- NFM-4822 — rerank degradation fix chain (PR #1352, PR #1353)
- NFM-3403 / NFM-3404 — error contract + timeout alignment (this ADR's
  framework)
- NFM-4492 — 30 s valve raised then walked back to 10 s
- NFM-4521 / NFM-4525 / NFM-4527 — qwen3.5:4b-nvfp4 swap, `think=False`,
  baked ollama binding
- NFM-4730-fixB — LLM token-cap infrastructure (cited by D5)
- NFM-4422 / NFM-4820 — pre-warm cron precedent (cited by D6)
