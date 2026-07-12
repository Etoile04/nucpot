/**
 * Typed API client for the KG Node Detail endpoint.
 *
 * Endpoint:
 *   GET /api/v1/kg/nodes/{type}/{id}
 *
 * Returns a single KG node with its incoming/outgoing edges grouped by
 * relation direction. Used by the /kg/nodes/[type]/[id] detail page.
 *
 * Spec: NFM-1337
 */

import { getToken } from "@/lib/api-client"

// ── Types ───────────────────────────────────────────────────────────

/** One directed edge to a neighbour node, paired with the neighbour's summary. */
export interface KgRelationItem {
  /** Edge UUID. */
  readonly edge_id: string
  /** Relation label (e.g. "hasProperty", "cites"). */
  readonly relation_type: string
  /** Direction relative to the focal node. */
  readonly direction: "outgoing" | "incoming"
  /** Edge confidence score (0.0–1.0). */
  readonly confidence: number
  /** The neighbour node summary (target of outgoing, source of incoming). */
  readonly neighbour: {
    readonly id: string
    readonly node_type: string
    readonly label: string
  }
}

/** Source provenance entry attached to the focal node. */
export interface KgSourceReference {
  readonly source_id: string | null
  readonly figure_id: string | null
  readonly label: string
}

export interface KgNodeDetail {
  readonly id: string
  readonly node_type: string
  readonly label: string
  readonly aliases: readonly string[]
  readonly properties: Record<string, unknown>
  readonly confidence: number
  readonly status: string
  readonly source_id: string | null
  readonly corpus_id: string | null
  readonly relations: {
    readonly incoming: readonly KgRelationItem[]
    readonly outgoing: readonly KgRelationItem[]
  }
  readonly sources: readonly KgSourceReference[]
}

// ── Helpers ─────────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  const token = getToken()
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }
  return headers
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: string
      message?: string
    }
    throw new Error(body.detail ?? body.message ?? `API error: ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── API ─────────────────────────────────────────────────────────────

export interface FetchKgNodeParams {
  readonly type: string
  readonly id: string
}

export function fetchKgNodeDetail(
  params: FetchKgNodeParams,
): Promise<KgNodeDetail> {
  const { type, id } = params
  if (!type || !id) {
    return Promise.reject(
      new Error("fetchKgNodeDetail requires both type and id"),
    )
  }
  return request<KgNodeDetail>(
    `/api/v1/kg/nodes/${encodeURIComponent(type)}/${encodeURIComponent(id)}`,
    { headers: authHeaders() },
  )
}