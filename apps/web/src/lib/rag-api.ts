/**
 * RAG chat API types and client for LightRAG query endpoint.
 *
 * NFM-741 (2026-07-25): aligned with the actual backend contract.
 * Previously this file assumed `{answer, citations, conversationId}` but
 * the backend returns `ApiResponse<QueryResponse>` where QueryResponse is
 * `{response, references, entities, relationships}` — and the shared
 * `request()` helper returns the raw envelope without unwrapping `.data`.
 * The mismatch made every RAG chat / semantic-search call render
 * `undefined`.  This file now unwraps the envelope and maps backend
 * fields to the legacy frontend shape so existing components keep working.
 *
 * NFM-3426 (2026-08-21): query timeout and failure copy now follow the
 * fast-fail contract. The abort budget is `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS`
 * (default 14s, previously a hardcoded 60s), and `ragApi.query()` rejects with
 * one of two canonical Chinese messages instead of a raw platform string. The
 * previous timeout copy named the 60s budget and told the user to shorten the
 * question — it blamed the user for a backend stall, and is gone.
 */

import { request, type ApiResponse } from "./api-client"
import {
  type RagContractQueryRequest,
  type RagContractQueryResponse as BackendQueryResponse,
  type RagContractReference as BackendReference,
} from "./rag-contract"

// ---------------------------------------------------------------------------
// Types — frontend-facing (unchanged shape, stable contract for components)
// ---------------------------------------------------------------------------

export interface RagCitation {
  readonly id: string
  readonly source: string
  readonly excerpt: string
  readonly confidence: number
  readonly url?: string
}

export interface RagMessage {
  readonly id: string
  readonly role: "user" | "assistant"
  readonly content: string
  readonly citations: readonly RagCitation[]
  readonly createdAt: string
}

export interface RagQueryRequest {
  readonly query: string
  readonly mode?: "naive" | "local" | "global" | "hybrid" | "mix"
  readonly conversationId?: string
  readonly topK?: number
}

export interface RagQueryResponse {
  /** Generated answer text (backend: `response`). */
  readonly answer: string
  /** Source references mapped from backend `references`. */
  readonly citations: readonly RagCitation[]
  /**
   * Client-generated conversation id. The backend has no conversation
   * memory (LightRAG is stateless per query), so the client owns this id
   * and threads it across turns for its own UI bookkeeping.
   */
  readonly conversationId: string
}

// ---------------------------------------------------------------------------
// Mapping helpers — transforms canonical backend fields to frontend shape
// ---------------------------------------------------------------------------

function mapReferenceToCitation(ref: BackendReference, idx: number): RagCitation {
  return {
    id: String(ref.reference_id ?? idx + 1),
    source: ref.file_path ?? "unknown",
    excerpt: ref.content ?? "",
    // LightRAG does not expose per-reference confidence; default to 1.0
    // so the UI confidence badge renders as "high" instead of crashing.
    confidence: 1.0,
  }
}

// ---------------------------------------------------------------------------
// Fast-fail contract (NFM-3426 — AC-1 / AC-4)
// ---------------------------------------------------------------------------

/**
 * Fallback query budget in milliseconds.
 *
 * The backend fast-fails well inside this window, so a query still
 * outstanding at 14s is stalled rather than merely slow. The previous 60s
 * budget left users watching a spinner for a full minute before any feedback.
 */
export const DEFAULT_RAG_QUERY_TIMEOUT_MS = 14_000

/** Shown when the client-side AbortController fires. */
export const RAG_TIMEOUT_MESSAGE =
  "查询超时，请稍后重试，或请尝试使用关键词搜索。"

/** Shown for every other failure mode (network, 5xx, success:false). */
export const RAG_UNAVAILABLE_MESSAGE =
  "语义检索暂时不可用，请稍后重试，或请尝试使用关键词搜索。"

/** Shown when the session expired and token refresh failed. */
export const RAG_AUTH_EXPIRED_MESSAGE =
  "登录已过期，请重新登录后再试。"

/**
 * Check whether an unknown error value is an auth-expired error.
 *
 * `api-client.ts` throws `new Error("认证已过期，请重新登录后重试")`
 * when the 401 refresh interceptor fails. This predicate lets callers
 * distinguish auth errors from other failures without inspecting raw text.
 */
export function isAuthExpiredError(err: unknown): boolean {
  if (typeof err !== "object" || err === null) return false
  const msg = (err as { message?: unknown }).message
  return typeof msg === "string" && msg.includes("认证已过期")
}

/**
 * Resolve the abort budget from `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS`.
 *
 * Next.js inlines `NEXT_PUBLIC_*` at build time, so this must stay a literal
 * member expression. Unset, empty, non-numeric, and non-positive values all
 * fall back to the default rather than collapsing to an instant abort —
 * `Number("")` is `0`, which would otherwise cancel before the request left.
 */
export function resolveRagQueryTimeoutMs(): number {
  const parsed = Number(process.env.NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS)
  return Number.isFinite(parsed) && parsed > 0
    ? parsed
    : DEFAULT_RAG_QUERY_TIMEOUT_MS
}

/**
 * `instanceof DOMException` fails across realms (jsdom, undici, workers), so
 * match on the name every abort implementation agrees on.
 */
function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "AbortError"
  )
}

/**
 * Pull the operator correlation id out of an error envelope.
 *
 * NFM-3407 stamps it top-level on `ApiResponse` as snake_case `request_id`;
 * the NFM-3426 brief describes it nested as `error.requestId`. Both shapes are
 * read so the id survives whichever one the backend settles on.
 */
function readRequestId(envelope: unknown): string | undefined {
  if (typeof envelope !== "object" || envelope === null) return undefined
  const record = envelope as Record<string, unknown>

  const topLevel = record.request_id ?? record.requestId
  if (typeof topLevel === "string" && topLevel !== "") return topLevel

  const error = record.error
  if (typeof error === "object" && error !== null) {
    const nested = error as Record<string, unknown>
    const id = nested.requestId ?? nested.request_id
    if (typeof id === "string" && id !== "") return id
  }

  return undefined
}

/** Append the correlation id so users can quote it when reporting a fault. */
function withRequestId(message: string, requestId?: string): string {
  return requestId ? `${message}（请求编号：${requestId}）` : message
}

/**
 * Issue the query, translating every transport-level failure into one of the
 * two friendly messages. Raw `err.message` never escapes here — upstream
 * tracebacks and "The operation was aborted" are not user-facing copy.
 */
async function fetchQueryEnvelope(
  backendPayload: RagContractQueryRequest,
  signal: AbortSignal,
): Promise<ApiResponse<BackendQueryResponse>> {
  try {
    return await request<ApiResponse<BackendQueryResponse>>(
      "/api/v1/lightrag/query",
      {
        method: "POST",
        body: JSON.stringify(backendPayload),
        signal,
      },
    )
  } catch (err: unknown) {
    if (isAbortError(err)) throw new Error(RAG_TIMEOUT_MESSAGE)
    // Auth-expired errors already carry user-safe copy from the refresh
    // interceptor — preserve them so the UI can show a login link.
    if (isAuthExpiredError(err)) throw err
    throw new Error(RAG_UNAVAILABLE_MESSAGE)
  }
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const ragApi = {
  /**
   * Query the LightRAG knowledge graph.
   *
   * Unwraps the `{success, data, error}` envelope and maps the backend
   * QueryResponse fields to the frontend RagQueryResponse shape.
   *
   * NFM-3426: the abort budget comes from `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS`
   * (default 14s, was a hardcoded 60s) and every rejection carries one of the
   * two canonical Chinese messages, optionally suffixed with the backend
   * correlation id. Callers can surface `err.message` directly — it is always
   * user-safe copy, never a raw platform or upstream string.
   */
  query: async (payload: RagQueryRequest): Promise<RagQueryResponse> => {
    const backendPayload: RagContractQueryRequest = {
      query: payload.query,
      // Backend QueryMode enum: naive|local|global|hybrid|mix. Default mix.
      mode: payload.mode ?? "mix",
      include_references: true,
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(
      () => controller.abort(),
      resolveRagQueryTimeoutMs(),
    )
    try {
      const envelope = await fetchQueryEnvelope(
        backendPayload,
        controller.signal,
      )
      if (!envelope.success || !envelope.data) {
        // `request()` already throws on non-2xx, so reaching here means a 2xx
        // with success=false — unusual but possible. The envelope is the only
        // place a correlation id survives, so read it before discarding.
        throw new Error(
          withRequestId(RAG_UNAVAILABLE_MESSAGE, readRequestId(envelope)),
        )
      }
      const data = envelope.data
      const citations = (data.references ?? []).map(mapReferenceToCitation)
      return {
        answer: data.response,
        citations,
        // Backend is stateless; let the caller pass a stable conversationId
        // or we mint one per call (single-shot search has no continuity).
        conversationId: payload.conversationId ?? crypto.randomUUID(),
      }
    } finally {
      clearTimeout(timeoutId)
    }
  },
} as const

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function createRagMessage(
  role: RagMessage["role"],
  content: string,
  citations: readonly RagCitation[] = [],
): RagMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    citations: [...citations],
    createdAt: new Date().toISOString(),
  }
}

export function confidenceLevel(score: number): "high" | "medium" | "low" {
  if (score >= 0.8) return "high"
  if (score >= 0.5) return "medium"
  return "low"
}

export function confidenceColor(level: ReturnType<typeof confidenceLevel>): string {
  switch (level) {
    case "high":
      return "#10b981"
    case "medium":
      return "#f59e0b"
    case "low":
      return "#ef4444"
  }
}
