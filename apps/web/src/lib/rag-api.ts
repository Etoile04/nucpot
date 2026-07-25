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
 */

import { request, type ApiResponse } from "./api-client"

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
// Backend response shape — matches QueryResponse in
// apps/api/src/nfm_db/schemas/lightrag.py
// ---------------------------------------------------------------------------

interface BackendReference {
  readonly reference_id?: string | number
  readonly file_path?: string
  readonly content?: string | null
}

interface BackendQueryResponse {
  readonly response: string
  readonly references?: readonly BackendReference[]
  readonly entities?: readonly unknown[]
  readonly relationships?: readonly unknown[]
}

// ---------------------------------------------------------------------------
// Mapping helpers
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
// API
// ---------------------------------------------------------------------------

export const ragApi = {
  /**
   * Query the LightRAG knowledge graph.
   *
   * Unwraps the `{success, data, error}` envelope and maps the backend
   * QueryResponse fields to the frontend RagQueryResponse shape.
   * Throws if `success === false` (preserves the throw-on-error contract
   * that `request()` already establishes for non-2xx responses).
   */
  query: async (payload: RagQueryRequest): Promise<RagQueryResponse> => {
    const backendPayload = {
      query: payload.query,
      // Backend QueryMode enum: naive|local|global|hybrid|mix. Default mix.
      mode: payload.mode ?? "mix",
      include_references: true,
    }
    const envelope = await request<ApiResponse<BackendQueryResponse>>(
      "/api/v1/lightrag/query",
      {
        method: "POST",
        body: JSON.stringify(backendPayload),
      },
    )
    if (!envelope.success || !envelope.data) {
      // The frontend ApiResponse type omits the backend's `error` field,
      // so read it defensively.  `request()` already throws on non-2xx, so
      // reaching here means a 2xx with success=false — unusual but possible.
      const errMsg = (envelope as { error?: string }).error
      throw new Error(errMsg ?? "LightRAG 查询失败")
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
