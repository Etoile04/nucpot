# ADR-NFM-3404: RAG Query Timeout Alignment + Fast-Fail User Feedback

**Status:** Proposed (CTO-authored, awaiting CPO/LE implementation)
**Date:** 2026-08-21
**Parent:** NFM-3357 (RAG service errors & truncated error messages)
**Depends on:** NFM-3403 (T1 — error-message contract; `done` 2026-08-21)

> **2026-09-09 update — NFM-4525 / NFM-4521 Path A follow-up:** §8 below
> documents the per-mode latency budget after the qwen3.5:4b-nvfp4 model
> swap (NFM-4521 Path A) plus the thinking-mode disable (NFM-4525). The
> failure-path ceiling in §2.1 (≤15 s wall-clock) is unchanged; the
> success-path ceiling is now **8 s wall-clock** for cached or fresh
> queries on the post-fix stack, vs the 30 s ceiling NFM-4492 raised the
> read budget to while Path A was being validated.

---

## 1. Context

Failed semantic search queries currently take **~90 seconds** before the user sees an error. The four timeout layers in the request chain are misaligned:

| Layer | Current | File:Line |
| --- | --- | --- |
| Upstream LLM call inside sidecar | **150 s** | `docker-compose.lightrag.yml:57` (`LLM_TIMEOUT`) |
| Backend httpx connect ceiling | 5 s (hardcoded) | `apps/api/src/nfm_db/services/lightrag_client.py:149` |
| Backend httpx read/write/pool | **8 s** | `apps/api/src/nfm_db/services/lightrag_client.py:44, 150-152` |
| Frontend AbortController | **60 000 ms** | `apps/web/src/lib/rag-api.ts:103` |
| **User-visible wait** | **~90 s** | (observed) |

The most likely 90 s cause: when the upstream LLM stalls, the LightRAG sidecar still answers HTTP 200 to its `/query` endpoint after `LLM_TIMEOUT=150 s` with whatever partial state it has, OR the read-timeout path is not actually firing as expected. The frontend's 60 s budget exceeds every backend ceiling, so the user always sees the long path.

The frontend's current error message — *"查询超时（60秒），请缩短问题后重试"* (translation: "Query timeout (60 s), please shorten the question and retry") — fails AC-4 because it suggests the user reformulate rather than fall back to text search.

There is no single source of truth for the timeouts: the values are scattered across Python source, the TypeScript frontend, and the Docker compose override.

---

## 2. Decision

### 2.1 Single source of timeout truth — environment variables

Add four documented env vars in `.env.lightrag` (and the API service env) so the topology is observable and adjustable in one place:

```
# --- Backend (apps/api) ---
NFM_LIGHTRAG_QUERY_TIMEOUT_S=12      # binding httpx read budget per query
NFM_LIGHTRAG_QUERY_CONNECT_S=3       # TCP handshake ceiling (must be < NFM_LIGHTRAG_QUERY_TIMEOUT_S)

# --- Frontend (apps/web) ---
NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS=14000  # AbortController (must be > NFM_LIGHTRAG_QUERY_TIMEOUT_S × 1000)

# --- Sidecar (docker-compose.lightrag.yml) ---
LIGHTRAG_LLM_TIMEOUT_S=8             # upstream LLM call inside the sidecar
                                     # (must be <= NFM_LIGHTRAG_QUERY_TIMEOUT_S - connect - safety)
```

**Two-tier reality — configured vs default (must read together):**

| Path | `LIGHTRAG_LLM_TIMEOUT_S` | `NFM_LIGHTRAG_QUERY_CONNECT_S` | `NFM_LIGHTRAG_QUERY_TIMEOUT_S` | `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS` | Algebraic invariant (§2.1) | AC-1 ≤15 s wall-clock |
| --- | --- | --- | --- | --- | --- | --- |
| Configured (operator copies `.env.lightrag.example`) | 8 | 3 | 12 | 14_000 | HOLDS — `8+3+1=12 ≤ 12 ≤ 14 ≤ 15` ✓ | ≤ 15 s |
| Default (env vars unset → module-constant fallback) | 8 | 5.0 | 8.0 | 14_000 | VIOLATED — `8+5+1=14 > 8` ✗ | ≤ 15 s (worst case: connect 5 s + read 8 s = 13 s, bounded by the 14 s frontend abort) |

**Algebraic invariant (HOLDS only on the configured path):**
```
LIGHTRAG_LLM_TIMEOUT_S + NFM_LIGHTRAG_QUERY_CONNECT_S + 1_safety
    <= NFM_LIGHTRAG_QUERY_TIMEOUT_S
    <= NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS / 1000
    <= 15   # AC-1
```

Configured values satisfy it: `8 + 3 + 1 = 12` ✓ ; `12 ≤ 14` ✓ ; `14 ≤ 15` ✓.

**Why this is NOT a release blocker:** the *user-visible* AC-1 bound (≤15 s wall-clock wait) holds in **both** paths because the httpx client itself bounds a single attempt to `connect + read` and the frontend AbortController cancels at 14 s. The default-path invariant violation is a budget-arithmetic observation about the *sum* exceeding the *read* tier; it does not change the worst-case wall-clock the user experiences. AC-5 (timeouts documented in one place, aligned) is satisfied structurally — the env-var block above is the single source of truth — even though the algebraic invariant only binds operators who copy the example env file.

**Default module constants (do not silently change without re-review):**

| Constant | Value | File |
| --- | --- | --- |
| `_DEFAULT_QUERY_TIMEOUT` | `8.0` | `apps/api/src/nfm_db/services/lightrag_client.py:44` |
| `_DEFAULT_QUERY_CONNECT_S` | `5.0` | `apps/api/src/nfm_db/services/lightrag_client.py:65` |
| `DEFAULT_RAG_QUERY_TIMEOUT_MS` | `14_000` | `apps/web/src/lib/rag-api.ts:94` |
| `LLM_TIMEOUT` (sidecar) | `${LIGHTRAG_LLM_TIMEOUT_S:-8}` | `docker-compose.lightrag.yml:57` |

Aligning `_DEFAULT_QUERY_TIMEOUT` `8.0 → 12.0` and `_DEFAULT_QUERY_CONNECT_S` `5.0 → 3.0` would make the algebraic invariant HOLDS on the default path too, but is a **behaviour-changing code change** that must return through Code Review — explicitly **out of scope** for this docs-only landing. Track in a follow-up issue if operator-onboarding friction becomes worth the change.

### 2.2 Layer-by-layer failure contract

| Layer | Budget | Failure mode | Result |
| --- | --- | --- | --- |
| 1. Sidecar → upstream LLM | 8 s | LLM_TIMEOUT fires inside sidecar | sidecar returns 5xx → `LightRAGClientError` at API |
| 2. Backend httpx connect | 3 s | TCP refused / DNS timeout | `httpx.ConnectError` → `LightRAGClientError` |
| 3. Backend httpx read/write/pool | 12 s | sidecar stalls mid-response | `httpx.ReadTimeout` → `LightRAGClientError` |
| 4. Frontend AbortController | 14 s | backend stalls | `AbortError` → translated message |

**Worst-case user wait:** 14 s + UI render = **≤ 15 s** (AC-1 ✓).

### 2.3 No silent retry on the query path

`apps/api/src/nfm_db/services/rag_provider.py:332-338` already does single-attempt-then-fallback. The fallback (`RuleBasedFallbackProvider.query`) is fast SQL — NOT the 90 s source. **Confirm (do not change):** no retry loop is added at any layer. AC-2 ✓.

For `/api/v1/lightrag/query` (the dedicated endpoint the frontend uses), the existing error-envelope response is correct — no rule-based fallback there (the frontend handles the user-facing "switch to text search" suggestion).

### 2.4 Fast-fail user feedback contract (AC-4)

`apps/web/src/lib/rag-api.ts` must:

1. Replace the literal `60_000` with `Number(process.env.NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS ?? 14_000)`.
2. Translate every caught error to a user-friendly Chinese message that includes:
   - The friendly cause ("语义检索暂时不可用" / "查询超时")
   - The actionable fallback ("请尝试使用关键词搜索")
   - The `requestId` from the backend error envelope (per NFM-3403 T1) when present
3. Never surface raw `err.message` to the UI.

Proposed message strings:

```
"查询超时，请稍后重试，或尝试使用关键词搜索。"
"语义检索暂时不可用，请稍后重试，或尝试使用关键词搜索。"
```

### 2.5 Health-check contract (AC-3)

The existing `docker-compose.lightrag.yml:83-90` healthcheck (`curl -fsS ... /health` every 15 s, 5 s timeout, 5 retries, 30 s `start_period`) is **kept** but the API's per-request probe is unnecessary — the 3-layer timeout chain above guarantees bounded user wait regardless of container health.

**Verification requirement (AC-3):** integration test must demonstrate that an unhealthy container (kill LLM container) causes the next query to fail fast (≤ 14 s) rather than time out at 90 s.

---

## 3. Files to Modify

| File | Change | Owner |
| --- | --- | --- |
| `docker-compose.lightrag.yml` | `LLM_TIMEOUT: ${LIGHTRAG_LLM_TIMEOUT_S:-8}` (was hardcoded 150) | LE |
| `.env.lightrag` (new if absent) | add 4 timeout vars with values from §2.1 | LE |
| `apps/api/src/nfm_db/services/lightrag_client.py` | read `_DEFAULT_QUERY_TIMEOUT` / connect from `NFM_LIGHTRAG_QUERY_TIMEOUT_S` / `NFM_LIGHTRAG_QUERY_CONNECT_S` env vars; fall back to module constants | LE |
| `apps/web/src/lib/rag-api.ts` | AbortController timeout from `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS`; new error-message strings per §2.4; pass `requestId` into UI message if present | Web LE |
| `apps/api/src/nfm_db/api/v1/lightrag.py` | unchanged (existing error envelope is correct) | — |
| `apps/api/src/nfm_db/services/rag_provider.py` | unchanged (single-attempt + fast SQL fallback is correct) | — |

---

## 4. Test Plan (AC-6)

**Backend (`apps/api/tests/`):**

- `test_lightrag_query_timeout.py` — mock `httpx.AsyncClient.post` to raise `httpx.ReadTimeout`; assert `LightRAGClient.query` raises `LightRAGClientError` within `1.2 × query_timeout` wall-clock; assert no retry occurred.
- `test_lightrag_query_connect_timeout.py` — mock connect refused; assert fails within `connect_timeout` budget.

**Frontend (`apps/web/src/lib/__tests__/rag-api.test.ts`):**

- `rag-api.spec.ts` — mock `fetch` to delay 20 s; assert `ragApi.query()` rejects with the new user-friendly Chinese message, NOT raw `"The operation was aborted"`.
- mock `success: false` envelope with `error.requestId`; assert message includes the requestId when surfaced.

**Integration / E2E:**

- `docker compose up` with `LIGHTRAG_LLM_TIMEOUT_S=2`; point an upstream mock LLM to hang; assert query returns error envelope within ≤ 15 s wall-clock; assert UI shows the "try text search" suggestion.

---

## 5. Acceptance Criteria Mapping

| AC | Requirement | How satisfied |
| --- | --- | --- |
| AC-1 | failed query returns error within 15 s | frontend 14 s + UI render ≤ 15 s; backend 12 s caps any single layer |
| AC-2 | no silent retry / slow fallback | confirmed `RAGProviderSelector.query()` is single-attempt; no `tenacity` / `asyncio.sleep` / retry loop in chain |
| AC-3 | health-check verified working | integration test stops the LLM container, asserts next query fails ≤ 14 s |
| AC-4 | user-friendly error + "try text search" | new frontend message string §2.4 + structured `requestId` field from NFM-3403 |
| AC-5 | timeouts documented in one place, aligned | `.env.lightrag` + invariant check in §2.1; existing 8 s / 60 s magic numbers replaced by env reads |
| AC-6 | existing tests pass; new test verifies fast-fail | pytest + vitest per §4; integration test for AC-3 |

---

## 6. Risks & Non-Goals

- **Risk:** widening backend timeout from 8 s → 12 s may slightly raise the median query latency on stalled cases. Mitigation: only the *fail* path is affected; the success path is unchanged.
- **Risk:** tightening AbortController from 60 s → 14 s means complex multi-hop graph queries that legitimately take > 14 s will now fail. Mitigation: the 60 s budget was always a fallback to a broken sidecar. The new 14 s budget is the correct semantic-failure ceiling; legitimate long queries should be re-engineered (async / polling) in a separate issue, NOT here.
- **Non-goal:** changing the success-path latency budget. This ADR is purely about failure-path alignment.
- **Non-goal:** adding a circuit breaker or persistent health cache. The previous circuit breaker (NFM-1247) was removed; the per-request try/except fallback is the canonical pattern.

---

## 7. References

- NFM-2565 — split read/write timeouts for query vs ingest paths (already shipped)
- NFM-3367 — `connect=5s` ceiling to prevent TCP handshake blowing budget (already shipped)
- NFM-3403 (T1) — full-exception + requestId error contract (shipped 2026-08-21)
- NFM-3357 — parent epic
- NFM-1222 — semantic query bridge at `/api/v1/kg/search?mode=lightrag`
- NFM-1247 — prior circuit-breaker removed in favour of stateless per-request fallback
- NFM-4492 — raised `NFM_LIGHTRAG_QUERY_TIMEOUT_S` from 8 s → 30 s as the
  temporary ceiling while Path A was being validated (superseded by the
  NFM-4525 fix below; can be walked back to 8–12 s after the post-fix
  per-mode budget is verified)
- NFM-4521 (Path A) — switched `PROD_LIGHTRAG_LLM_MODEL` from
  `qwen3.8:27b-mlx` to `qwen3.5:4b-nvfp4` (NFM-4521 PR #1268) to bring the
  *cached*-query wall-clock under 1 s; latent thinking-mode bug only
  surfaced under fresh (non-cached) queries
- NFM-4525 — switches `PROD_LIGHTRAG_LLM_BINDING` from `openai` (compat)
  to `ollama` (native) and installs `docker/lightrag/sitecustomize.py`
  to default `think=False` on the Ollama binding; brings fresh-query
  wall-clock back inside the 30 s ceiling without touching NFM-4492's
  raised timeout
- NFM-4527 — NFM-4525's image shipped **inert** because `docker/lightrag.Dockerfile`
  did not install the `ollama` Python client. lightrag's `pipmaster.core`
  lazy-installs the binding's package at container START against
  `pypi.org` (GFW-blocked from inside the container) and loops forever on
  `Failed to handle package: ollama`; `nucpot-prod-lightrag` stays
  `health: starting` indefinitely and `sitecustomize.py` never gets to run.
  NFM-4527 bakes `ollama>=0.6.0` and `httpx` into the image at build time
  so pipmaster never gets a chance to lazy-install. A regression test in
  `docker/lightrag/tests/test_dockerfile_binding_package.py` enforces the
  binding-package → Dockerfile invariant for any future `LLM_BINDING=<x>`
  flip.

---

## 8. Per-mode latency budget (post NFM-4521 Path A + NFM-4525 fix)

This section is the operator-facing reference for the prod LightRAG
sidecar after NFM-4521 Path A swapped the model to `qwen3.5:4b-nvfp4` and
NFM-4525 disabled the model's thinking mode. All numbers are wall-clock
observed end-to-end on prod (`/api/v1/lightrag/query`) with
`NFM_LIGHTRAG_QUERY_TIMEOUT_S=30` (NFM-4492 ceiling).

| Mode | Cache hit? | Expected wall-clock (post NFM-4525) | Failure ceiling (still applies from §2.1) |
| --- | --- | --- | --- |
| `hybrid` (default) | yes | **0.1–0.3 s** (cached extraction) | 30 s |
| `hybrid` | no | **1–5 s** (fresh extraction w/o thinking) | 30 s |
| `local` | yes | 0.1–0.3 s (cached) | 30 s |
| `local` | no | 1–3 s (entity-anchored retrieval, smaller graph) | 30 s |
| `global` | yes | 0.1–0.3 s (cached) | 30 s |
| `global` | no | 3–7 s (full-graph traversal) | 30 s |
| `naive` | yes | 0.1–0.3 s (cached) | 30 s |
| `naive` | no | 1–3 s (pure vector retrieval, no graph) | 30 s |
| `mix` | yes | 0.1–0.3 s (cached) | 30 s |
| `mix` | no | 2–6 s (hybrid-of-hybrids) | 30 s |

**Pre-NFM-4525 (latent bug, observed during Path A validation):**

| Mode | Cache hit? | Observed wall-clock (pre-fix) | Root cause |
| --- | --- | --- | --- |
| any | no | **30.0 s** (timeout ceiling, empty `Content` | qwen3.5:4b-nvfp4 eats output tokens on internal `Thinking Process: 1. Analyze the Request:…` reasoning before producing user-visible content; Ollama's `/v1/chat/completions` compat layer ignores `chat_template_kwargs` / `extra_body.think` / `options.think` so the openai binding could not disable it |
| any | yes | 0.1 s (cached answer text) | extraction was cached by an earlier session whose token budget predates the thinking-mode-active templates |

**Why the post-fix budget holds:**

1. NFM-4525 switched `PROD_LIGHTRAG_LLM_BINDING` to `ollama` (native
   `/api/chat`) which honors the top-level `think` body parameter.
2. `docker/lightrag/sitecustomize.py` is auto-loaded by Python at
   `lightrag-server` startup and monkey-patches
   `lightrag.llm.ollama._ollama_model_if_cache` to default
   `think=False`, with `setdefault` semantics so any per-call override
   (e.g. future role-LLM kwargs) is preserved.
3. With thinking disabled, `qwen3.5:4b-nvfp4` answers "say hi in 5
   words" in 6 tokens / 2.8 s on the Ollama daemon (measured). Hybrid
   query overhead (vector retrieval + graph traversal + prompt assembly)
   dominates; per-query latency is in the 1–7 s range observed above.
4. The `OPENAI_LLM_REASONING_EFFORT=none` env that NFM-4521 Path A set
   is now redundant (it controls OpenAI `o1`-style reasoning effort, not
   qwen3 thinking mode); kept on the container for audit-trail parity,
   to be cleaned up in a follow-up.

**Walking back NFM-4492's 30 s ceiling:** after this fix, the per-mode
ceiling in §2.1 (8 s LLM + 3 s connect + 12 s read + 14 s frontend =
≤ 15 s user-visible) is once again the operative budget. The NFM-4492
raise to 30 s was a safety valve while Path A was being validated; it
can be walked back to 12 s in a follow-up that re-runs the §4 integration
test against the post-NFM-4525 image. Tracked but not in scope for
this docs-only landing.

### 8.1 Addendum — actual post-fix wall-clock (NFM-4527)

> **2026-09-09 update — NFM-4527 shipped:** NFM-4525's image
> (`aa0db03e48c6fc77ae9601b86004c03d53312f32`) deployed to prod but the
> fix was inert — `docker/lightrag.Dockerfile` did not install the
> `ollama` Python client. Cached queries still returned in 0.1–2.5 s
> (extraction cached before thinking-mode-active templates landed),
> but **fresh unique queries still hit the 30 s ceiling** with empty
> `Content`, because `nucpot-prod-lightrag` itself never finished
> initialising past pipmaster. NFM-4527 bakes the binding's package
> into the image at build time. After NFM-4527 deploy + env flip,
> the per-mode wall-clock should track the budget table in §8 above.

| Mode | Cache hit? | Wall-clock after NFM-4527 deploy | Verified by |
| --- | --- | --- | --- |
| `hybrid` (default) | yes | **0.1–0.3 s** | cached-extraction regression check (NFM-4492) |
| `hybrid` | no | **1–5 s** (target) | fresh-hybrid smoke query with UNIQUE text — see NFM-4527 AC-4 |
| `local` / `naive` | no | 1–3 s | follow-up smoke queries |
| `global` | no | 3–7 s | follow-up smoke queries (multi-LLM-call, §3.1 risk) |
| `mix` | no | 2–6 s | follow-up smoke queries |

**Validation gate for the env flip:** before flipping
`PROD_LIGHTRAG_LLM_BINDING=ollama` (and dropping `/v1` from
`PROD_LIGHTRAG_LLM_HOST`), RE must verify on the post-NFM-4527 image:

1. `docker logs nucpot-prod-lightrag` contains
   `[nucmd-patch] LightRAG ollama binding patched: think=False default`
   within 60 s of `starting`.
2. Container reaches `health: healthy` within 2 min (vs indefinite
   `starting` on NFM-4525's image).
3. Fresh `hybrid` query with UNIQUE text returns
   `wall ≤ 30 s ∧ response_len > 100 chars ∧ finish_reason != length`.

If (1) or (2) fails, revert `docker/.env.prod` (LE rollback path
`run-recovery.sh rollback --tag <previous-sha>`) and treat the next
Dockerfile candidate as a no-deploy until the regression test in
`docker/lightrag/tests/test_dockerfile_binding_package.py` passes
locally with the updated `BINDING_PACKAGE_MAP`.
