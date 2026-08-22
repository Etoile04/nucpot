# ADR-NFM-3404: RAG Query Timeout Alignment + Fast-Fail User Feedback

**Status:** Proposed (CTO-authored, awaiting CPO/LE implementation)
**Date:** 2026-08-21
**Parent:** NFM-3357 (RAG service errors & truncated error messages)
**Depends on:** NFM-3403 (T1 — error-message contract; `done` 2026-08-21)

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
