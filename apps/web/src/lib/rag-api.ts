/**
 * RAG chat API types and client for LightRAG query endpoint.
 */

import { request } from "./api-client"

// ---------------------------------------------------------------------------
// Types
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
  readonly conversationId?: string
  readonly topK?: number
}

export interface RagQueryResponse {
  readonly answer: string
  readonly citations: readonly RagCitation[]
  readonly conversationId: string
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const ragApi = {
  query: (payload: RagQueryRequest): Promise<RagQueryResponse> =>
    request<RagQueryResponse>("/api/v1/lightrag/query", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
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
